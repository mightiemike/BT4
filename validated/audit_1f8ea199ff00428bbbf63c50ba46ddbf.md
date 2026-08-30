### Title
Future oracle timestamps are treated as always-fresh and permanently ratchet the monotonic price floor forward, freezing price resolution - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
### Finding Description
`oracle-timestamp-fresh` in `mainnet/contracts/market/v0-4-market.clar` (lines 365-371, mirrored in `local-testing/contracts/market/market.clar:387-393`) computes staleness as:

```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
``` [1](#0-0) 

When the feed's reported `ts` (Pyth `publish-time` or DIA timestamp, see `resolve-pyth`/`resolve-dia`) is greater than `stacks-block-time`, `delta` is forced to `u0`, so the freshness check `(<= delta max-staleness)` is trivially satisfied — a future timestamp is never rejected, unlike a stale one. [2](#0-1) 

This value is then persisted as the new monotonic floor in `price-resolve`:
```clarity
;; update timestamp if newer
(if (> timestamp last-update-time)
    (map-set last-update key timestamp)
    false)
``` [3](#0-2) 

Once a future `timestamp` is written to `last-update` for a given `{type, ident}` key, every subsequent (correctly-timed) price submission for that same feed must satisfy `(>= ts prev)` in `oracle-timestamp-fresh`. Because `prev` is now set in the future, any legitimately-timed update (`ts` ≈ real `stacks-block-time`) will have `ts < prev` and fail with `ERR-ORACLE-INVARIANT`, until real chain time catches up to the injected value. This is the direct analog of the reported bug: an unvalidated/unbounded timestamp is accepted, is used to advance a clock-like state variable, and that advanced state later blocks legitimate operations that depend on it — the same mechanism as the ExecutionPayload timestamp ratcheting the chain's execution clock forward and halting future block proposals.

### Impact Explanation
`price-resolve`/`price-multi-resolve` gate essentially all price-dependent market operations: `collateral-add` (capacity check), `collateral-remove`, `borrow`, `liquidate` (via `get-assets`/`get-notional-evaluation`/`process-collateral-asset`). [4](#0-3) [5](#0-4) 
If the monotonic floor for an asset's oracle feed is pushed into the future, all borrow/liquidate/collateral operations that require a fresh price for that asset will revert until real time passes the injected future timestamp. For collateral this can strand borrower positions unliquidatable (temporary freezing of funds/collateral) and for debt assets it can block new borrows/repayments for that asset. This lands in the in-scope **High** impact bucket (temporary freezing of funds), and if the injected timestamp is far enough in the future (bounded only by `uint` size), the freeze duration could be made effectively unbounded, pushing toward **Critical** (protocol insolvency risk from being unable to liquidate undercollateralized positions during the freeze).

### Likelihood Explanation
The likelihood hinges on whether an attacker (rather than only a legitimate Pyth/DIA publisher) can supply a `ts` greater than current `stacks-block-time` for a feed consumed by `price-resolve`. Pyth prices reach the market via `pyth-storage-v4`, whose `write-batch-entry` only enforces a **lower bound** on `publish-time` (`>= latest-stacks-timestamp - stale-price-threshold + STACKS_BLOCK_TIME`) and no upper bound, so a Pyth-signed update with a publish-time ahead of `stacks-block-time` (e.g., from minor clock skew between Wormhole guardians and the Stacks chain, or a compromised/malicious relayer submitting a valid but future-dated VAA) would pass and be forwarded unchanged into `market`'s `price-resolve`. [6](#0-5)  I was not able to fully confirm, within the indexed content, whether Wormhole/Pyth VAA verification independently bounds `publish-time` against real-world/chain time before reaching `pyth-storage-v4`, nor could I locate a `dia-oracle` contract in this repo to assess who can push DIA timestamps — these are open questions that materially affect exploitability and should be verified against the full contract set. Given that root cause is purely in Zest's own `oracle-timestamp-fresh` logic (the asymmetric `u0`-clamping of future deltas) rather than in oracle-signature validation, this stays in scope as an oracle-consumption bug in Zest's code rather than "incorrect data from a third-party oracle."

### Recommendation
Reject timestamps that are ahead of `stacks-block-time` instead of treating them as automatically fresh, e.g.:
```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (and
    (<= ts stacks-block-time)              ;; reject future timestamps outright
    (<= (- stacks-block-time ts) max-staleness)
    (>= ts prev)))
```
Additionally, consider bounding how far `last-update` can be advanced per call (e.g., cap `map-set last-update` at `min(timestamp, stacks-block-time)`) so a single bad/malicious upstream timestamp cannot permanently ratchet the floor beyond real time.

### Proof of Concept
1. A Pyth relayer (or a party able to produce a validly-signed VAA, e.g. via clock skew or compromised feed) submits a price update for asset `X` with `publish-time = T_future` where `T_future > current stacks-block-time`, and it passes `pyth-storage-v4`'s lower-bound-only staleness check, getting stored via `write-batch-entry`. [7](#0-6) 
2. A user calls `market.borrow`/`collateral-add`/`liquidate` with `price-feeds` causing `write-feeds` to forward this update into `price-resolve` for asset `X`. `resolve-pyth` returns `timestamp = T_future`. [8](#0-7) 
3. `oracle-timestamp-fresh(T_future, last-update-time, max-staleness)` computes `delta = 0` (since `T_future > stacks-block-time`), passing the freshness assertion trivially. [1](#0-0) 
4. `price-resolve` writes `map-set last-update {type, ident} T_future` since `T_future > last-update-time`. [3](#0-2) 
5. Any subsequent, correctly-timed price update for the same `{type, ident}` key has `ts ≈ stacks-block-time < T_future = prev`, failing `(>= ts prev)` in `oracle-timestamp-fresh`, so `price-resolve` reverts with `ERR-ORACLE-INVARIANT` for every caller until real chain time passes `T_future`.
6. During this window, `borrow`, `collateral-add`, `collateral-remove` (when the user has debt), and `liquidate` for positions involving asset `X` all revert, freezing user funds/collateral tied to asset `X`.

**Caveat:** I could not verify within the indexed files whether the Wormhole/Pyth verification pipeline independently bounds `publish-time` against real time before it reaches `pyth-storage-v4`, nor find the `dia-oracle` contract to check its access control — a Devin session with full repository access would be needed to confirm the exact upstream trust boundary and finalize exploitability with certainty.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L312-330)
```text
(define-private (resolve-pyth (ident (buff 32)))
  (let ((response (try! (call-pyth ident)))
        (price (get price response))
        (expo (get expo response))
        (conf (get conf response))
        (final-price (normalize-pyth price expo))
        (timestamp (get publish-time response)))
    (try! (check-confidence price conf))
    (ok { value: final-price, timestamp: timestamp })))

(define-private (call-dia (key (string-ascii 32)))
  (let ((res (unwrap! (contract-call? 'SP1G48FZ4Y7JY8G2Z0N51QTCYGBQ6F4J43J77BQC0.dia-oracle get-value key) ERR-ORACLE-DIA)))
    (ok res)))

(define-private (resolve-dia (ident (buff 32)))
  (let ((key (unwrap-panic (from-consensus-buff? (string-ascii 32) ident)))
        (res (try! (call-dia key))))
    ;; DIA returns timestamp in milliseconds, convert to seconds for staleness check
    (ok { value: (get value res), timestamp: (/ (get timestamp res) u1000) })))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L365-371)
```text
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L390-393)
```text
    ;; update timestamp if newer
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)
```

**File:** mainnet/contracts/market/v0-4-market.clar (L482-492)
```text
(define-private (get-assets (mask-user uint))
  (let ((mask-enabled (get-enabled-bitmap))
        (safe-mask (user-safe-mask mask-user mask-enabled))
        (iter (mask-to-list-collateral safe-mask))
        (assets-list (get-status-multi iter))
        (oracles-list (map get-oracle assets-list))
        ;; Extract asset-ids for price resolution
        (asset-ids (map get-asset-id assets-list))
        ;; Use internal price resolution
        (prices-list (unwrap-panic (price-multi-resolve oracles-list asset-ids))))
    (map merge-price assets-list prices-list)))
```

**File:** local-testing/contracts/market/market.clar (L1411-1436)
```text
                (collateral-receiver (optional principal))
                (price-feeds (optional (list 3 (buff 8192)))))
  (let (
    (feeds-check (try! (write-feeds price-feeds)))
    (liquidator contract-caller)
    (position (try! (get-liquidation-position borrower)))
    (pos-full (try! (get-full-position borrower)))
    (mask (get mask position))
    (group (try! (get-egroup mask)))

    (coll-address (contract-of collateral-ft))
    (debt-address (contract-of debt-ft))
    (coll-asset (try! (get-asset coll-address)))
    (debt-asset (try! (get-asset debt-address)))
    (coll-aid (get id coll-asset))
    (debt-aid (get id debt-asset))

    ;; accrue FIRST - populates cache for zToken price resolution
    (u-debt (accrue-user-debts (get debt pos-full)))
    (u-coll (accrue-user-collateral (get collateral pos-full)))

    ;; NOW safe to resolve prices (cache is populated)
    (assets (get-assets mask))
    (notional-valued-assets (get-notional-evaluation { position: position, assets: assets }))
    (total-collateral-usd (get collateral notional-valued-assets))
    (total-debt-usd (get debt notional-valued-assets))
```

**File:** local-testing/contracts/pyth/contracts/pyth-storage-v4.clar (L74-102)
```text
(define-private (write-batch-entry (entry {
		price-identifier: (buff 32),
		price: int,
		conf: uint,
		expo: int,
		ema-price: int,
		ema-conf: uint,
		publish-time: uint,
		prev-publish-time: uint,
	}))
	(let ((stale-price-threshold (contract-call? .pyth-governance-v3 get-stale-price-threshold))
			(latest-stacks-timestamp (unwrap! (get-stacks-block-info? time (- stacks-block-height u1)) ERR_STALE_PRICE))
			(publish-time (get publish-time entry)))
		;; Ensure that we have not processed a newer price
		(asserts! (is-price-update-more-recent (get price-identifier entry) publish-time) ERR_NEWER_PRICE_AVAILABLE)
		;; Ensure that price is not stale
		(asserts! (>= publish-time (+ (- latest-stacks-timestamp stale-price-threshold) STACKS_BLOCK_TIME)) ERR_STALE_PRICE)
		;; Update storage
		(map-set prices 
			(get price-identifier entry) 
			{
				price: (get price entry),
				conf: (get conf entry),
				expo: (get expo entry),
				ema-price: (get ema-price entry),
				ema-conf: (get ema-conf entry),
				publish-time: publish-time,
				prev-publish-time: (get prev-publish-time entry)
			})
```
