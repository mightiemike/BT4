### Title
Monotonic oracle timestamp ratchet permanently DoS's price resolution (and thus liquidations/borrows) after a single future-dated price update - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`price-resolve` gates every oracle price lookup with `oracle-timestamp-fresh`, which stores the last accepted timestamp in `last-update` and only ever accepts a strictly-increasing timestamp. Because the freshness check treats any timestamp greater than the current `stacks-block-time` as automatically "fresh" (`delta = 0`), a single price update carrying a future `publish-time` is accepted and permanently ratchets `last-update` forward. Every subsequent legitimate update — carrying a normal, current timestamp — is smaller than this poisoned `prev` value and is rejected with `ERR-ORACLE-INVARIANT` until real time catches up, bricking price resolution (and everything that depends on it: borrow, liquidate, health checks) for that asset.

### Finding Description
`price-resolve` in `mainnet/contracts/market/v0-4-market.clar` (lines 373-395 in the mirrored `local-testing` copy, identical logic in `mainnet/contracts/market/v0-4-market.clar` lines 360-395) validates every resolved price with: [1](#0-0) 

```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

and then: [2](#0-1) 

```clarity
(define-private (price-resolve
  (data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (let (...
        (last-update-time (oracle-last-update key))
        (timestamp (get timestamp resolution))
        (max-staleness (get max-staleness data)))
    (asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
              ERR-ORACLE-INVARIANT)
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)
    (ok final-price)))
```

`last-update` is a monotonic ratchet: it only ever moves forward via `map-set last-update key timestamp`, and there is no mechanism anywhere in the contract to reset or lower it. The freshness function has a logic flaw: when `ts > stacks-block-time` (a future-dated publish timestamp), `delta` is forced to `u0`, which trivially satisfies `<= delta max-staleness` regardless of how far in the future `ts` is. Combined with `(>= ts prev)`, a single future-timestamped price update — from either the Pyth relay path (`resolve-pyth`/`pyth-storage-v4` `publish-time`) or the DIA path (`resolve-dia`) — is accepted as "fresh" and immediately becomes the new `prev` stored in `last-update`.

From that point forward, every subsequent legitimate price update (with a normal, real-time `publish-time`) fails `(>= ts prev)` because `prev` is now a value in the future, so `oracle-timestamp-fresh` returns `false` and `price-resolve` unconditionally reverts with `ERR-ORACLE-INVARIANT`, for as long as real time has not caught up to the poisoned future value (which can be set arbitrarily far ahead, since the check never bounds how far into the future `ts` may be).

This mirrors the analog bug class exactly: a cached "high-water mark" value (`last-update`) is advanced without any validation against real-world bounds, and is never invalidated when its source (an errant or malicious oracle publish) turns out to be wrong; every later legitimate use of the oracle is blocked by comparison against this stale/poisoned cached value.

### Impact Explanation
Once `last-update` for a feed is poisoned with a future timestamp, `price-resolve` reverts for every call that needs that asset's price, in a single subsequent transaction (no multi-block collusion, no cross-user interference needed — it's a single relay/oracle transaction followed by any later use). This blocks:
- `borrow`/`withdraw` health checks that rely on `get-notional-evaluation` → `price-multi-resolve` → `price-resolve`.
- `liquidate` (`mainnet/contracts/market/v0-4-market.clar`, `liquidate` function), since it calls `write-feeds`/price resolution before computing `current-ltv`.

Because liquidations for the affected asset are unconditionally reverted while the poisoned future timestamp persists, insolvent positions cannot be liquidated, exposing lenders/vault depositors to bad debt accumulation, i.e., temporary freezing of the liquidation/borrow pathway. If the poisoned timestamp is set far enough in the future (there is no upper bound check), the freeze can persist indefinitely — a durable liveness failure for the affected market asset, which can result in unclaimed yield/loss exposure for depositors while bad debt accrues unliquidated.

### Likelihood Explanation
Triggering requires only a single price-update transaction (via the Pyth relayer path into `pyth-storage-v4`, or DIA) carrying a `publish-time` greater than the current `stacks-block-time` — this could be caused by a misconfigured/faulty relayer clock skew, or by anyone permitted to submit oracle updates upstream. The check that is supposed to guard against implausible timestamps (`oracle-timestamp-fresh`) actively *waives* the staleness bound in exactly the case that should be most suspicious (future timestamps), making the trigger condition easy to hit accidentally and trivial to hit intentionally by anyone with oracle-write access.

### Recommendation
1. In `oracle-timestamp-fresh`, reject (rather than special-case to zero delta) any `ts` that exceeds `stacks-block-time` by more than a small allowed clock-skew tolerance, instead of silently treating it as "fresh."
2. Bound how far `last-update` can be advanced per update (e.g., disallow accepting a `timestamp` that is unreasonably far ahead of `stacks-block-time`), so a single bad price update cannot ratchet the high-water mark far into the future.
3. Add an admin/governance recovery path to reset `last-update` for a feed if it becomes poisoned, so the market is not permanently stuck waiting for real time to catch up.

### Proof of Concept
1. Oracle relayer (Pyth `write`/`write-batch-entry` in `pyth-storage-v4`, or the DIA oracle) submits a price update for asset X whose `publish-time`/timestamp is set (accidentally due to clock skew, or deliberately) to a value greater than the current `stacks-block-time`.
2. A user or contract calls a function that triggers `price-resolve` for asset X (e.g., `borrow`, `liquidate`, or any function calling `get-notional-evaluation`).
3. `oracle-timestamp-fresh` computes `delta = u0` (because `ts > stacks-block-time`), passes `<= delta max-staleness` trivially, and passes `(>= ts prev)` since `prev` was smaller; `price-resolve` accepts this timestamp and executes `map-set last-update key timestamp`, storing the future value.
4. Any later legitimate call resolves asset X's real price with the current, real `publish-time`. Since this real timestamp is less than the poisoned `last-update` entry, `oracle-timestamp-fresh` returns `false`, and `price-resolve` reverts with `ERR-ORACLE-INVARIANT`.
5. All subsequent borrow/withdraw/liquidate calls needing asset X's price revert until real time reaches the poisoned future timestamp, blocking liquidation of any position using asset X as collateral or debt during that window.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L362-371)
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
```

**File:** mainnet/contracts/market/v0-4-market.clar (L373-395)
```text
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
