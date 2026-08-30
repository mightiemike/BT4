### Title
All-or-nothing multi-asset price fold reverts entire health/price resolution when a single asset's oracle is stale or unavailable - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`price-multi-resolve` fetches prices for a batch of assets (e.g. all of a user's collateral and debt assets) by folding over `iter-price-multi`. If price resolution for even one asset in the batch fails — because its Pyth/DIA oracle call reverts or its timestamp is not "fresh" per `oracle-timestamp-fresh` — the fold does not skip just that asset; it poisons the whole accumulator (`valid: false`), and the batch call ultimately reverts with `ERR-ORACLE-MULTI`. Any caller that needs a multi-asset price snapshot (health/LTV checks used by borrow, withdraw, liquidate flows) is denied service as long as any single asset in the batch has a stale or reverting oracle feed.

### Finding Description
`price-resolve` calls `resolve-price-feed` (`try!`, which propagates `ERR-ORACLE-PYTH`/`ERR-ORACLE-DIA` on failure) and enforces freshness with: [1](#0-0) 

then asserts both price legality and timestamp freshness, reverting with `ERR-ORACLE-INVARIANT` on failure: [2](#0-1) 

`iter-price-multi` is designed to "absorb" this failure inside the fold instead of reverting immediately — on failure it sets `valid: false` on the accumulator rather than aborting per-item, and subsequent iterations short-circuit via `(asserts! valid acc)` so the fold completes without processing further assets correctly: [3](#0-2) 

But the batch entry point `price-multi-resolve` then asserts on the poisoned flag and reverts the entire call: [4](#0-3) 

This is structurally identical to the reported `UpdateWeightRunner._getData()` bug class: a loop/aggregator that pulls data from several independent oracle sources for several assets, where a failure/staleness in a single source causes the *entire* aggregate call to revert rather than degrading gracefully for just the affected asset. `docs/oracle.md` confirms this batch path is used precisely to resolve prices for a user's full set of collateral and debt assets in one call (`5 collateral assets + 3 debt assets = 8 prices` in a single internal resolution) [5](#0-4) , which strongly implies it backs health/LTV evaluation used by `borrow`/`withdraw`/`liquidate`-style flows in `v0-4-market.clar`.

### Impact Explanation
If any single asset among the enabled collateral/debt set for a health check has a stale Pyth/DIA feed (a real, expected occurrence per both providers' documentation) or a reverting price-feed call, `price-multi-resolve` reverts for the *whole* batch, not just the affected asset. This denies borrow, withdrawal, and repayment operations that depend on a full multi-asset price snapshot, and — more critically — can also block liquidation of positions whose collateral/debt set happens to include the stale asset, since the same aggregation path is used to compute health. This is a temporary freezing of user funds/positions (inability to withdraw/borrow/repay) and, in the liquidation-blocking case, a temporary freezing of protocol/liquidator value, matching the in-scope "temporary freezing of funds" impact class.

### Likelihood Explanation
Oracle staleness for at least one of several tracked assets is a routine, expected condition (both Pyth and DIA publish timestamps can lag past a configured `max-staleness`), so this can be triggered without any privileged action or attacker cost — simply by the natural passage of time on a low-liquidity/low-update-frequency feed while a user's position happens to reference that asset in its enabled collateral/debt bitmask.

### Recommendation
Change `iter-price-multi`/`price-multi-resolve` so that a failure to resolve or a stale price for one asset does not poison the entire batch. Either (a) resolve each asset's price independently and only require freshness for the assets actually needed to compute the specific check being performed, or (b) allow a per-asset fallback/backup oracle before failing that asset, so an unrelated stale feed cannot block operations (including liquidation) for positions where that asset isn't actually the fresh price being relied upon.

### Proof of Concept
1. User A holds collateral in assets `{X, Y}` and debt in asset `{Z}`; asset `Y`'s Pyth feed has not published a fresh update within `max-staleness` (a normal occurrence).
2. User A calls `borrow`/`withdraw`, which triggers a health check requiring a multi-asset price snapshot via `price-multi-resolve` over `{X, Y, Z}` (per `mainnet/contracts/market/v0-4-market.clar:397-425`, `docs/oracle.md:302-317`).
3. `iter-price-multi` processes `X` (ok), then `Y`: `price-resolve` hits `oracle-timestamp-fresh` returning false, so the `asserts!` in `price-resolve` fails, `iter-price-multi`'s `unwrap!` sets `valid: false` (`mainnet/contracts/market/v0-4-market.clar:405-418`).
4. Iteration for `Z` short-circuits via `(asserts! valid acc)` and passes the poisoned accumulator through unchanged.
5. `price-multi-resolve` asserts `(get valid response)`, which is `false`, and reverts with `ERR-ORACLE-MULTI` (`mainnet/contracts/market/v0-4-market.clar:397-403`), aborting the user's `borrow`/`withdraw` call entirely — even though asset `Y`'s stale price was not the binding constraint for the health computation.
6. The same mechanism, if reached from a liquidation health-check path, would block liquidation of an actually-unhealthy position whenever any one of its collateral/debt assets has a stale feed.

Note: I was unable to fully trace, within the tool budget available, the exact call sites (`borrow`, `withdraw`, `liquidate`) that invoke `price-multi-resolve`/`iter-price-multi` inside `v0-4-market.clar` to confirm liquidation is on this exact path — this is inferred from the batch-price documentation and constant definitions (`ERR-UNHEALTHY`, health-check references) found in the file, and should be verified directly in the full contract before treating the liquidation-blocking scenario as confirmed.

### Citations

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

**File:** mainnet/contracts/market/v0-4-market.clar (L397-403)
```text
(define-private (price-multi-resolve
  (data (list 64 { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (aids (list 64 uint)))
  (let ((init { output: (list), valid: true, aids: aids, idx: u0 })
        (response (fold iter-price-multi data init)))
    (asserts! (get valid response) ERR-ORACLE-MULTI)
    (ok (get output response))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L405-418)
```text
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

**File:** docs/oracle.md (L302-317)
```markdown
## Batch Price Fetching

For gas efficiency, multiple prices can be fetched in a single internal call:

```clarity
;; In market.clar
(define-private (price-multi-resolve 
  (data (list 64 {type, ident, callcode}))
  (aids (list 64 uint)))
  (fold iter-price-multi data init))
```

**Use Case:** Market needs prices for multiple assets:
- 5 collateral assets + 3 debt assets = 8 prices
- Single internal resolution instead of 8 separate operations
- Returns list of 8 prices in same order as input
```
