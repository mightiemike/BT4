### Title
Market's per-block index cache is not invalidated when a vault's index is mutated out-of-band by `set-pause-states`, allowing stale interest indexes to be used for health/liquidation math within the same block - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`market.clar` maintains a per-block cache of each vault's borrow/liquidity index (`index-cache`, keyed by `{ timestamp: stacks-block-time, aid }`) so that multiple operations in the same block avoid repeated cross-contract `accrue` calls. [1](#0-0)  The cache is populated by calling the vault's `accrue` and is treated as authoritative for the rest of the block, on the assumption that a vault's index cannot change except through that same `accrue-and-cache` path. However, a vault's index/lindex can also be mutated directly by the vault's own `set-pause-states`, which calls the vault's local `accrue()` (bypassing the market's cache) whenever accrual is being paused, in order to "capture pending interest" before locking the state. [2](#0-1)  If the market already cached that vault's index earlier in the same block (before the DAO's pause transaction lands), the market's cache is never invalidated, so every subsequent market operation in that block (borrow, collateral-add/remove, liquidation, notional evaluation) keeps using the stale pre-pause index instead of the just-updated one.

### Finding Description
1. `market.clar`'s `accrue-and-cache` looks up `index-cache` keyed only by `(stacks-block-time, aid)`. On a cache hit it returns the previously stored `{index, lindex}` without ever re-querying the vault: [1](#0-0) 
2. This cached value is consumed throughout the same-block transaction lifecycle for debt/collateral accrual (`accrue-user-debts`, `accrue-user-collateral`) and for notional/health evaluation (`calculate-asset-notional-value` uses `accrue-and-cache` to get the borrow index for debt valuation). [3](#0-2) [4](#0-3) 
3. A vault's `index`/`lindex` data-vars are the vault's "source of truth" and can be mutated directly via the vault's own `accrue()`, independent of the market's cache map. `set-pause-states` explicitly triggers this direct mutation when transitioning `accrue` from unpaused to paused, precisely to flush pending interest into `index`/`lindex` before the freeze: [5](#0-4) 
4. Because `index-cache` in `market.clar` has no dependency on the vault's `pause-states` or any invalidation hook tied to `set-pause-states`, a cache entry written earlier in the block (before the pause transaction) is not invalidated by the pause transaction's direct `accrue()` call. Any subsequent market call in the same block that hits `accrue-and-cache` for that `aid` returns the old, pre-pause index rather than the just-flushed one.
5. This is a textbook "cached value not invalidated when its source moves" bug: the bound value is the `{index, lindex}` tuple stored in `index-cache` for `(stacks-block-time, aid)`; the invalidating event is the vault's direct `accrue()` call inside `set-pause-states`; the later use is any `accrue-and-cache` read for the same `aid` within the same block (in `borrow`, `collateral-add`, `collateral-remove`, `liquidate`, or health checks).

### Impact Explanation
Debt valuation and health checks performed after the stale-cache read will understate the actual accrued debt for the affected asset for the remainder of the block. This can allow a borrower to pass a health check and borrow/withdraw more than they should be able to, or allow a position that should be liquidatable (fully or partially) to appear healthy and evade liquidation until the next block. Both scenarios correspond to unclaimed-yield/temporary freezing-style impact (understated debt improperly extends borrowing capacity or blocks timely liquidation, temporarily freezing lenders' entitled interest/collateral recovery) — landing in the in-scope **High** impact class (temporary freezing of unclaimed yield, or theft of unclaimed yield via under-accrued debt).

### Likelihood Explanation
This requires two things to line up within the same block: (a) an ordinary user operation on the affected vault's asset earlier in the block that primes the market's `index-cache` for that `(timestamp, aid)`, and (b) a `set-pause-states` call on that vault transitioning `accrue` from false→true later in the same block. Pausing accrual is a normal, legitimate governance/operational action (e.g., in response to an oracle or vault incident), not a compromise of the DAO — so the trigger condition is realistic operationally, though it depends on transaction ordering within a single block (mempool/ordering influence), which is achievable by any actor who can also transact in the same block (including the pausing party or an opportunistic user monitoring the mempool).

### Recommendation
Invalidate (or bypass) `market.clar`'s `index-cache` entry for an asset whenever that vault's `accrue()` is invoked outside of the market's own `accrue-and-cache` path — e.g., by removing the affected `(timestamp, aid)` entry from `index-cache` inside `set-pause-states` before/after its direct `accrue()` call, or by having the market always re-derive the index from the vault rather than trusting a block-scoped cache that vault-side logic can independently mutate. Alternatively, route all vault index mutations (including the pause-triggered flush) through the same cache-aware helper the market uses, so the cache and the vault's on-chain state can never diverge within a block.

### Proof of Concept
1. Block N, Tx1 (any user): calls a market operation (e.g., `borrow`) on asset X, which calls `accrue-and-cache` for `aid = X`; cache miss → market calls `vault-X.accrue()`, caches `{index: I0, lindex: L0}` under `{timestamp: T, aid: X}` in `index-cache`. [1](#0-0) 
2. Block N, Tx2 (DAO/governance): calls `vault-X.set-pause-states({accrue: true, ...})`. Since `accrue` transitions false→true, the vault directly calls its own `accrue()`, which recomputes and stores an updated `index: I1 > I0` (capturing interest accrued since the last update) directly in the vault's data vars, entirely bypassing `market.clar`'s cache. [5](#0-4) 
3. Block N, Tx3 (any user, e.g., the same borrower or another user with debt on asset X): calls a market operation that triggers `accrue-and-cache` for `aid = X` again (e.g., another `borrow`, `collateral-remove`, or a health check inside `liquidate`). Since `{timestamp: T, aid: X}` is already present in `index-cache` from Tx1, this returns the stale `{index: I0, lindex: L0}` instead of the vault's now-correct `I1`. [6](#0-5) 
4. Debt valuation in `calculate-asset-notional-value` for that user's position on asset X uses the stale `I0` instead of `I1`, understating the position's real debt-in-USD for the remainder of block N, letting an unhealthy position pass a health check (borrow more / evade liquidation) that it would fail with the correct, already-flushed index. [4](#0-3) 

*Note: full confirmation that no other code path re-synchronizes `index-cache` immediately after `set-pause-states` was not exhaustively verified beyond the files inspected above; a Devin session with full repo access could trace all call sites of `index-cache`/`accrue-and-cache` to confirm no compensating invalidation exists elsewhere.*

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L245-257)
```text
(define-private (accrue-and-cache (aid uint))
  (let ((cache-key { timestamp: stacks-block-time, aid: aid })
        (cached? (map-get? index-cache cache-key)))

    (match cached?
      ;; cache HIT: return cached value (1 read only)
      cached-indexes (ok cached-indexes)

      ;; cache MISS: accrue and cache (vault-accrue now returns indexes)
      (let ((indexes (try! (vault-accrue aid))))
        ;; store in cache
        (map-set index-cache cache-key indexes)
        (ok indexes)))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L259-293)
```text
(define-private (accrue-user-debts (debt-list (list 64 { aid: uint, scaled: uint})))
  (fold accrue-debt-asset debt-list { success: true }))

(define-private (accrue-debt-asset
  (debt-entry { aid: uint, scaled: uint })
  (acc { success: bool }))
  (begin
    ;; this will use cache if available, accrue if not
    (unwrap-panic (accrue-and-cache (get aid debt-entry)))
    acc))

(define-private (accrue-user-collateral (coll-list (list 64 {aid: uint, amount: uint})))
  (fold accrue-collateral-asset coll-list { success: true }))

(define-private (accrue-collateral-asset
  (coll-entry { aid: uint, amount: uint })
  (acc { success: bool }))
  (let ((aid (get aid coll-entry)))
    ;; Only accrue if asset is a registered ztoken
    (if (is-ztoken aid)
        ;; ZToken: map to underlying vault routing ID and accrue
        ;; zSTX(1)->STX(0), zsBTC(3)->sBTC(2), zstSTX(5)->stSTX(4), zUSDC(7)->USDC(6), zUSDH(9)->USDH(8), zstSTXbtc(11)->stSTXbtc(10)
        (let ((vault-id (if (is-eq aid zSTX) STX
                        (if (is-eq aid zsBTC) sBTC
                        (if (is-eq aid zstSTX) stSTX
                        (if (is-eq aid zUSDC) USDC
                        (if (is-eq aid zUSDH) USDH
                        (if (is-eq aid zstSTXbtc) stSTXbtc
                        ;; will cause ERR-UNKNOWN-VAULT with any value over 64
                        u100))))))))
          (begin
            (unwrap-panic (accrue-and-cache vault-id))
            acc))
        ;; Non-ztoken: skip accrual (no liquidity index needed)
        acc)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L564-569)
```text
        (debt-notional (if (> debt-scaled u0) ;; use cache instead here
                           (let ((cached (unwrap-panic (accrue-and-cache asset-id)))
                                 (ib (get index cached))
                                 (actual (mul-div-up debt-scaled ib INDEX-PRECISION)))
                             (normalize (* actual price) decimals true))
                           u0)))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L723-737)
```text
(define-public (set-pause-states (states {deposit: bool, redeem: bool, borrow: bool, repay: bool, accrue: bool, flashloan: bool}))
  (begin
    (try! (check-dao-auth))
    (let ((current (var-get pause-states))
          (was-paused (get accrue current))
          (now-paused (get accrue states)))
      ;; When pausing accrue, accrue first to capture pending interest
      (if (and (not was-paused) now-paused)
          (begin (try! (accrue)) false)
          false)
      ;; When unpausing accrue, jump last-update to now to skip paused period
      (if (and was-paused (not now-paused))
          (var-set last-update stacks-block-time)
          false)
      (var-set pause-states states)
```
