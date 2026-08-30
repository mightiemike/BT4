Confirmed — no upper-bound cap on the oracle timestamp exists anywhere else in `v0-4-market.clar`; the only freshness gate is `oracle-timestamp-fresh` at lines 365-371.

### Title
Future-dated oracle timestamp poisons the monotonic `last-update` cache, permanently bypassing staleness checks and freezing price resolution - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`price-resolve` in `v0-4-market.clar` gates every oracle price on `oracle-timestamp-fresh`, which only bounds a timestamp that is *older* than the current block. A timestamp that is *newer* than `stacks-block-time` is silently treated as `delta = u0` (maximally fresh) and is unconditionally accepted, with no upper-bound sanity check. Because the accepted timestamp is then written into the monotonic `last-update` map and all future price updates must be `>= ts prev` [1](#0-0) , a single out-of-range future timestamp becomes a persistent floor that legitimate, correctly-timed future price updates cannot clear until real block time (or oracle clock) catches up to the bogus value.

### Finding Description
The freshness/staleness check is:
```
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time) u0 (- stacks-block-time ts))))
    (and (<= delta max-staleness) (>= ts prev))))
``` [2](#0-1) 

This is invoked from `price-resolve`, which — if the check passes — advances the stored baseline:
```
(asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness)) ERR-ORACLE-INVARIANT)
(if (> timestamp last-update-time) (map-set last-update key timestamp) false)
``` [3](#0-2) 

`last-update` is the cached "source of truth" bound at the time of the last accepted price and is read back on the *next* call as `prev` via `oracle-last-update` [4](#0-3) . There is no assertion anywhere in the contract that `ts` is bounded above (e.g., `<= stacks-block-time + tolerance`) — the only guard silently degrades to a no-op for future timestamps instead of reverting.

Sequence:
1. Bind: a price feed update (Pyth or DIA) is resolved with `publish-time = ts_bad`, where `ts_bad` is anomalously far in the future relative to `stacks-block-time` (clock drift between Pythnet/DIA publisher time and Stacks block time, or a corrupted/glitched feed value forwarded by the external oracle contract).
2. `price-resolve` computes `delta = u0` (because `ts_bad > stacks-block-time`), trivially satisfying `<= max-staleness`, and `ts_bad >= prev` also holds since it is a new high-water mark. The invariant assertion passes.
3. The event that should invalidate/reject this value (an upper-bound freshness check) never fires, so `map-set last-update key ts_bad` commits the bad future value as the new floor.
4. Later use: every subsequent legitimate oracle update for that feed must satisfy `ts_new >= ts_bad` (the `prev` value read from the poisoned `last-update` map). Any correctly-timed real update with `ts_new < ts_bad` is rejected with `ERR-ORACLE-INVARIANT`, even though it is objectively the freshest legitimate data.
5. All market operations that require this asset's price (`borrow`, `liquidate`, `collateral-add`/`collateral-remove` egroup/health checks) revert until the real timestamp catches up to `ts_bad`, which can be an arbitrarily long window depending on how far in the future the poisoning value was.

This is the single-block/single-transaction "value bound → invalidating event skipped → stale/poisoned value used later" pattern: the cached `last-update` timestamp is never invalidated because the guard that should catch out-of-range future values silently degrades instead of asserting.

### Impact Explanation
This lands on **temporary freezing of funds** (High): while a feed is poisoned, users cannot `borrow` against, `collateral-add`/`collateral-remove`, or be `liquidate`d for the affected asset because `price-resolve` reverts with `ERR-ORACLE-INVARIANT` for all callers on that asset until real time (or the oracle's own clock) advances past the poisoned value. If liquidations are blocked for an asset whose collateral is simultaneously crashing in price, this escalates toward protocol insolvency (Critical) since undercollateralized positions cannot be liquidated during the freeze window.

### Likelihood Explanation
This does not require compromising any private key or governance process — it only requires one out-of-range `publish-time`/timestamp value to be forwarded through `resolve-pyth`/`resolve-dia` and accepted by `price-resolve` (e.g., via natural publisher/network clock skew, a glitched update, or a single anomalous data point). Once accepted, the effect is deterministic and persists automatically without further attacker action, driven purely by the contract's own monotonic-timestamp bookkeeping logic in `v0-4-market.clar`.

### Recommendation
Add an explicit upper-bound assertion in `oracle-timestamp-fresh` (or before it) that rejects timestamps materially ahead of `stacks-block-time` (e.g., `(asserts! (<= ts (+ stacks-block-time SOME_TOLERANCE)) ERR-ORACLE-INVARIANT)`) instead of silently mapping future timestamps to `delta = u0`. This prevents any future-dated value — malicious or accidental — from ever being written into the monotonic `last-update` cache, eliminating the possibility of poisoning the freshness floor for later legitimate updates.

### Proof of Concept
1. Deployer/DAO configures asset `A` with `max-staleness = 120` seconds via the asset registry.
2. At `stacks-block-time = T`, a Pyth/DIA update for `A` is resolved with `timestamp = T + 100000` (either due to publisher clock skew or a corrupted feed value); `resolve-price-feed` returns this timestamp unmodified to `price-resolve`.
3. `oracle-timestamp-fresh(T+100000, prev, 120)` computes `delta = u0` (since `T+100000 > T`), passes `<= 120`, and passes `>= prev`; `ERR-ORACLE-INVARIANT` is not raised.
4. `map-set last-update {type, ident} (T+100000)` commits.
5. At real time `T+1000` (still far less than `T+100000`), a fresh, correct Pyth update with `timestamp = T+1000` arrives. `oracle-timestamp-fresh(T+1000, T+100000, 120)` fails `(>= ts prev)` since `T+1000 < T+100000`; `price-resolve` reverts `ERR-ORACLE-INVARIANT`.
6. Any call to `borrow`, `liquidate`, or `collateral-add`/`collateral-remove` involving asset `A` now reverts on price resolution until real block time passes `T+100000`, freezing all operations dependent on `A`'s price for that entire window.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L128-130)
```text
;; Write a single Pyth price feed update using fold accumulator pattern
(define-private (write-feed (feed (buff 8192)) (status (response bool uint)))
  (match status
```

**File:** mainnet/contracts/market/v0-4-market.clar (L365-395)
```text
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
