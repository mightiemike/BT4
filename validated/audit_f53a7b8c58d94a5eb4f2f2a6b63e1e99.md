### Title
Uncaught `to-uint` runtime error on negative/overflowing oracle price permanently freezes borrow/withdraw/liquidate for that asset - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`normalize-pyth` and `check-confidence` in the market contract cast a signed `int` price coming straight from the external Pyth oracle contract into `uint` with `to-uint`, without first validating that the value is non-negative or that intermediate arithmetic (`pow`, `*`) stays in range. `to-uint` on a negative operand (or an out-of-range `pow`/multiplication result) throws an unrecoverable Clarity runtime error that cannot be caught by `try!`/`match`/`asserts!` inside the contract, aborting every transaction that reaches this code path.

### Finding Description
`resolve-pyth` fetches `{price, expo, conf}` from the Pyth storage contract and immediately feeds the raw signed `price` into `normalize-pyth`: [1](#0-0) 

```
(define-private (normalize-pyth (p int) (expo int))
  (let ((adj (+ expo 8))
        (inkind? (asserts! (not (is-eq adj 0)) (to-uint p)))
        (res (if (> adj 0) (* p (pow 10 adj)) (/ p (pow 10 (- adj))))))
    (to-uint res)))

(define-private (check-confidence (price int) (confidence uint))
  (ok (asserts! (<= confidence (/ (* (to-uint price) (var-get max-confidence-ratio)) BPS)) ERR-PRICE-CONFIDENCE-LOW)))
```

Both functions call `to-uint` on the raw `int` `price`/`p` value returned by the oracle, with no prior `(>= p 0)` guard. In Clarity, `to-uint` applied to a negative `int` (or `pow`/`*` producing a value outside `int128` range before the cast) is a native runtime error, not a `(err ...)` response — it is not interceptable by `try!`, `unwrap!`, `match`, or `asserts!` inside the calling contract. It unconditionally aborts the entire transaction.

This value is not validated before use the way it would need to be: the only guard that exists is `oracle-price-legal`, which runs *after* `normalize-pyth`/`check-confidence` have already executed and potentially aborted: [2](#0-1) [3](#0-2) 

The guard `oracle-price-legal` (`(> p u0)`) is only reachable if `to-uint` inside `normalize-pyth`/`check-confidence` didn't already abort — i.e., the "mutation" (unsafe downcast) is evaluated before the guard that was meant to police it, mirroring the report's core defect ("downcast happens before validity is confirmed").

`price-resolve` is the single chokepoint used by every asset-price-dependent operation (health checks, borrow, withdraw, liquidate) via `price-multi-resolve`/`iter-price-multi`: [4](#0-3) 

Since `iter-price-multi` calls `price-resolve` directly (not wrapped in anything that can catch a native runtime error), any single asset whose Pyth feed returns a negative `price` (or an `expo` producing an out-of-range `pow`) makes every multi-asset price resolution that includes that asset abort unconditionally, for every caller, until the Pyth feed value changes or governance intervenes.

### Impact Explanation
Because `price-resolve`/`price-multi-resolve` is the shared entry point for collateral valuation across borrow, withdraw, and liquidation flows, an out-of-range signed price value for any single supported asset (STX, sBTC, stSTX, USDC, USDH, stSTXbtc) freezes borrow/withdraw/liquidate for every user whose position touches that asset, since the transaction aborts before any `err`-based handling can run. This is a temporary freezing of funds (impact class: temporary freezing of funds) until the upstream oracle value moves back into range or the contract is patched/redeployed — exactly the "downcasting halts execution for everyone" failure mode described in the report, adapted to Clarity's single-transaction-abort semantics rather than node-level consensus halt.

### Likelihood Explanation
Likelihood is low, matching the original report's rating: Pyth typically returns positive prices for the supported crypto pairs, and `expo` is normally in a narrow, sane range. However, Pyth's price schema permits negative `price` values in general (and `conf`/`expo` are also oracle-supplied), and there is no on-chain sanity check in this contract preventing such a value from being consumed before the cast. The bug is not "oracle gives wrong data" in the excluded sense (third-party depeg) — it's this contract's own missing guard around a native down/type-cast operation on oracle-sourced input, which stays in scope per the rules.

### Recommendation
Validate the sign/range of `p` (and `price` in `check-confidence`) with a checked comparison (`(>= p 0)`) via `asserts!`/`ok`/`err` *before* calling `to-uint`, so that an out-of-range value produces a normal `(err ...)` (e.g., `ERR-ORACLE-INVARIANT`) instead of an unrecoverable runtime abort. Apply the same guard to any other unchecked `to-uint`/`to-int` conversions of oracle- or externally-sourced signed values in this contract.

### Proof of Concept
1. Pyth's on-chain storage contract (`pyth-storage-v4`) is updated (via a valid, correctly-signed Pyth VAA/message) with a price feed entry for e.g. sBTC where `price` is negative (or `expo` is such that `pow 10 adj` overflows `int128`) — this is data the oracle is technically allowed to publish, not a bug the exploiter needs to create in this contract.
2. Any user calls a market entrypoint that needs to price sBTC — `collateral-add`/`borrow`/`withdraw`/`liquidate` — which internally calls `price-multi-resolve` → `iter-price-multi` → `price-resolve` → `resolve-pyth` → `normalize-pyth`/`check-confidence`.
3. `to-uint p` (or `to-uint res`) inside `normalize-pyth`, or `to-uint price` inside `check-confidence`, hits a negative operand and throws a native Clarity runtime error.
4. The runtime error is not a `(err ...)` value, so `try!` in `resolve-pyth`/`price-resolve` cannot intercept it; the whole transaction aborts unconditionally, before `oracle-price-legal` (the intended guard) is ever evaluated.
5. Every transaction from every user that includes sBTC in its price-resolution list now reverts the same way, for as long as the bad price sits in `pyth-storage-v4` — freezing borrow, withdraw, and liquidation for all positions holding sBTC collateral or debt.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L297-320)
```text
(define-private (normalize-pyth (p int) (expo int))
  (let ((adj (+ expo 8))
        (inkind? (asserts! (not (is-eq adj 0)) (to-uint p)))
        (res (if (> adj 0)
                (* p (pow 10 adj))
                (/ p (pow 10 (- adj))))))
    (to-uint res)))

(define-private (check-confidence (price int) (confidence uint))
  (ok (asserts! (<= confidence (/ (* (to-uint price) (var-get max-confidence-ratio)) BPS)) ERR-PRICE-CONFIDENCE-LOW)))

(define-private (call-pyth (ident (buff 32)))
  (let ((res (unwrap! (contract-call? 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-storage-v4 get-price ident) ERR-ORACLE-PYTH)))
    (ok res)))

(define-private (resolve-pyth (ident (buff 32)))
  (let ((response (try! (call-pyth ident)))
        (price (get price response))
        (expo (get expo response))
        (conf (get conf response))
        (final-price (normalize-pyth price expo))
        (timestamp (get publish-time response)))
    (try! (check-confidence price conf))
    (ok { value: final-price, timestamp: timestamp })))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L362-395)
```text
(define-private (oracle-price-legal (p uint))
  (> p u0))

(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))

(define-private (price-resolve
  (data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (let ((type (get type data))
        (ident (get ident data))
        (key { type: type, ident: ident })
        (resolution (try! (resolve-price-feed type ident)))
        (price (get value resolution))
        (callcode (get callcode data))
        (final-price (try! (resolve-callcode price callcode)))
        (last-update-time (oracle-last-update key))
        (timestamp (get timestamp resolution))
        (max-staleness (get max-staleness data)))

    ;; validate price and timestamp using max-staleness from oracle data
    (asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
              ERR-ORACLE-INVARIANT)

    ;; update timestamp if newer
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)

    (ok final-price)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L397-418)
```text
(define-private (price-multi-resolve
  (data (list 64 { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (aids (list 64 uint)))
  (let ((init { output: (list), valid: true, aids: aids, idx: u0 })
        (response (fold iter-price-multi data init)))
    (asserts! (get valid response) ERR-ORACLE-MULTI)
    (ok (get output response))))

(define-private (iter-price-multi
  (oracle-data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint })
  (acc { output: (list 64 uint), valid: bool, aids: (list 64 uint), idx: uint }))
  (let ((valid (get valid acc))
        (skip? (asserts! valid acc))
        (asset-ids (get aids acc))
        (idx (get idx acc))
        ;; resolve price - will use cache for ztokens
        (price (unwrap! (price-resolve oracle-data) (merge acc { valid: false })))
        (next (unwrap-panic (as-max-len? (append (get output acc) price) u64))))
    { output: next,
      valid: true,
      aids: asset-ids,
      idx: (+ idx u1) }))
```
