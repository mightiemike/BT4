### Title
Market's per-block index-cache is not invalidated when a vault's liquidity index is written down by `socialize-debt`, allowing overvalued zToken collateral to be borrowed against within the same block - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`market.clar` maintains a read-through cache, `index-cache`, keyed by `{timestamp: stacks-block-time, aid: aid}`, that stores each vault's `{index, lindex}` pair to avoid repeated cross-contract `vault-accrue` calls within the same block. [1](#0-0)  The cache is populated lazily on first access per block and is only invalidated when `stacks-block-time` changes (i.e., a new block). [2](#0-1)  However, a vault's `lindex` can also be mutated directly through `socialize-debt`, which writes down the liquidity index to reflect a bad-debt loss, bypassing the market's cache-update path entirely. [3](#0-2)  Because the market's cache key only changes on a new block timestamp, any transaction later in the same block that hits the cache (rather than re-querying the vault) will use the pre-write-down `lindex`, overvaluing the corresponding zToken.

### Finding Description
1. `accrue-and-cache` in `market.clar` checks `index-cache` for `{timestamp: stacks-block-time, aid}`; on a cache hit it returns the stored `{index, lindex}` without calling the vault again. [2](#0-1) 
2. Collateral/debt accrual for a ztoken (`accrue-collateral-asset`) and debt accrual (`accrue-debt-asset`) both route through `accrue-and-cache`, so once cached for a given `(timestamp, aid)`, all subsequent same-block operations reuse the value. [4](#0-3) 
3. Separately, `market.clar` exposes `vault-socialize-debt`, which dispatches directly to the underlying vault's `socialize-debt` entry point during bad-debt handling. [5](#0-4) 
4. Inside the vault, `socialize-debt` recomputes and writes a new, lower `lindex` proportional to the loss, directly via `var-set`, with no call back into market's `index-cache` map. [3](#0-2) 
5. Because the market's cache key is `{timestamp: stacks-block-time, aid}` and Stacks blocks batch multiple transactions under one `stacks-block-time`, a cache entry populated earlier in the block is not automatically refreshed when the vault's real `lindex` changes later in the same block from `socialize-debt`.

**Sequence:**
1. Transaction A (block N): any market operation on ztoken `aid` (e.g. a small deposit/borrow/health-check) triggers `accrue-and-cache(aid)`, caching the pre-loss `{index, lindex}` for `{timestamp: T, aid}`.
2. Transaction B (same block N, same `T`): a liquidation on a different, undercollateralized position triggers `vault-socialize-debt(aid, amount)`, causing the vault to write down its real `lindex` to reflect the bad-debt loss - the market cache is untouched.
3. Transaction C (same block N, same `T`): a borrower uses zToken `aid` as collateral. Market calls `accrue-and-cache(aid)` again; this is a cache HIT against the entry from step 1, so the stale, pre-loss (higher) `lindex` is used for oracle price/collateral valuation instead of the vault's real, written-down value.
4. The borrower's collateral is valued using the inflated `lindex`, allowing them to borrow more than the true value of their zToken collateral actually supports.

### Impact Explanation
This lets a borrower extract debt against collateral that is not actually backed by the underlying assets, since the true collateral value has already been reduced by the socialized bad debt but the market still prices it at the stale, higher index. This directly risks protocol insolvency and theft of funds beyond what genuine collateral supports, since positions can be opened knowingly undercollateralized relative to the vault's actual state.

### Likelihood Explanation
Exploitation requires an attacker (or an opportunistic actor) to interleave transactions within the same block around a `socialize-debt` event: prime the cache before the write-down, then borrow immediately after it in the same block, before the timestamp/cache-key rolls to the next block. Bad-debt socialization events are lower frequency but predictable (liquidations of severely underwater positions), and an attacker monitoring the mempool could time a borrow transaction into the same block right after observing/triggering the liquidation, making this practically exploitable rather than purely theoretical.

### Recommendation
Invalidate or refresh the market's `index-cache` entry for the affected `aid` whenever `vault-socialize-debt` (or any other direct vault-state mutation path such as bad-debt write-down) is invoked, e.g. by having `vault-socialize-debt` call `map-set index-cache` with the fresh indexes immediately after the vault call returns, or by removing the block-scoped cache assumption and re-deriving `lindex` from the vault whenever a socialization event has occurred in the current block.

### Proof of Concept
1. Block N, Tx1: Attacker (or any user) performs a trivial operation touching ztoken `zsBTC` (e.g., depositing 1 unit as collateral) which calls `accrue-and-cache(sBTC)`, caching `lindex = L0` for `{timestamp: T, aid: sBTC}`. [6](#0-5) 
2. Block N, Tx2: A liquidator liquidates a severely underwater position with unrecoverable bad debt in `sBTC`, triggering `vault-socialize-debt`, which lowers the vault's real `lindex` to `L1 < L0` to reflect the loss. [5](#0-4) [3](#0-2) 
3. Block N, Tx3: Attacker deposits `zsBTC` as collateral and borrows against it. Market calls `accrue-and-cache(sBTC)` again, hits the cache from Tx1, and values the attacker's `zsBTC` using the stale `L0` instead of the real `L1`, letting the attacker borrow more than their `zsBTC` is actually worth post-socialization.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L112-118)
```text
;; -- Index cache (for accrual)
(define-map index-cache
  { timestamp: uint, aid: uint }
  { index: uint, lindex: uint })

;; -- Oracle timestamp tracking
(define-map last-update
```

**File:** mainnet/contracts/market/v0-4-market.clar (L216-223)
```text
(define-private (vault-socialize-debt (aid uint) (amount uint))
  (if (is-eq aid STX) (contract-call? .v0-vault-stx socialize-debt amount)
  (if (is-eq aid sBTC) (contract-call? .v0-vault-sbtc socialize-debt amount)
  (if (is-eq aid stSTX) (contract-call? .v0-vault-ststx socialize-debt amount)
  (if (is-eq aid USDC) (contract-call? .v0-vault-usdc socialize-debt amount)
  (if (is-eq aid USDH) (contract-call? .v0-vault-usdh socialize-debt amount)
  (if (is-eq aid stSTXbtc) (contract-call? .v0-vault-ststxbtc socialize-debt amount)
  ERR-UNKNOWN-VAULT)))))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L245-293)
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

**File:** local-testing/contracts/vault/vault-ststxbtc.clar (L948-960)
```text
(define-public (socialize-debt (scaled-amount uint))
  (let ((scaled-principal (var-get principal-scaled))
        (borrowed (var-get total-borrowed))
        (idx (var-get index))
        (current-assets (var-get assets))
        (current-lindex (var-get lindex))
        (old-total-assets (total-assets))
        (debt-reduction (mul-div-down scaled-amount idx INDEX-PRECISION))
        (principal-reduction (if (> scaled-principal u0)
                                (mul-div-down scaled-amount borrowed scaled-principal)
                                u0))
        ;; Write down lindex proportionally to loss in total-assets
        (new-lindex (if (and (> old-total-assets u0) (> old-total-assets debt-reduction))
```
