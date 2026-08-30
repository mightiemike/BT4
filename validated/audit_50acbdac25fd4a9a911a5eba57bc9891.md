### Title
Stale `index-cache` `lindex` after `socialize-debt` write-down enables borrowing/health-checks against overvalued zToken collateral in the same block - (File: mainnet/contracts/market/v0-4-market.clar)

### Summary
`market.clar`'s `accrue-and-cache` caches each vault's `{index, lindex}` pair keyed only by `{timestamp: stacks-block-time, aid}`. `stacks-block-time` is constant for the whole block, so once a value is cached for a vault in a block, every subsequent call within that same block returns the cached tuple on a cache HIT without re-reading vault state. The vault's `socialize-debt` function, however, writes down `lindex` directly with `var-set lindex new-lindex`, completely bypassing `accrue()`/`accrue-and-cache`. Because the cache key never changes within a block, this write-down is invisible to the market's cache: any collateral valuation, health check, or borrow that reads the cached `lindex` for that vault later in the same block continues to use the pre-write-down (higher) value.

### Finding Description
`accrue-and-cache` implements a per-timestamp cache: [1](#0-0) 

On a cache HIT it returns the previously stored `{index, lindex}` without consulting the vault at all, and the cache key is `{timestamp: stacks-block-time, aid}` — identical for every transaction inside the same block.

Separately, `socialize-debt` in each vault (e.g. `v0-vault-stx.clar`) mutates `lindex` directly to write down the vault's liquidity index after realizing a loss, without going through `accrue`: [2](#0-1) 

Sequence:
1. Earlier in the block/transaction, `market.clar` calls `accrue-and-cache(aid)` for some vault (e.g. as part of a health check, price resolution, or accrual fold over `debt-list`/`coll-list`), which caches `{index, lindex}` under `{timestamp, aid}`.
2. A liquidation on that same vault realizes bad debt and the market calls `vault-socialize-debt`, which writes a lower `lindex` directly into vault storage (line 963 of `v0-vault-stx.clar`), but never touches `index-cache-`.
3. Any later read in the same block — a health check for the liquidated borrower's remaining zToken collateral, a different user's borrow/health check against zToken collateral of that same asset, or even a subsequent step of the same liquidation transaction — calls `accrue-and-cache(aid)` again and gets a cache HIT, returning the stale, higher pre-write-down `lindex` instead of the corrected value.

The cached value (vault `lindex`) is the source of truth for pricing zToken (rehypothecated) collateral; `socialize-debt` is the event that invalidates it (moves it downward); the later use is any zToken valuation reading the stale cache entry within the same block. This is a single-block cache-invalidation gap, not a cross-user interference mechanism: the root cause is purely that one code path (`socialize-debt`) mutates state that another code path (`accrue-and-cache`) treats as immutable for the remainder of the block.

### Impact Explanation
zToken collateral (`zsBTC`, `zSTX`, etc.) prices scale with the vault's `lindex`. Using a stale, inflated `lindex` after a socialize-debt write-down causes zToken collateral to be overvalued in subsequent health checks for the rest of the block. This can let a borrower draw out more debt than their true (post-loss) collateral value supports, i.e. borrow against a valuation the protocol itself just proved incorrect — a direct path to under-collateralized debt and fund extraction, landing on the Critical impact class (theft of user funds / protocol insolvency), since positions can be pushed under real collateralization while still appearing healthy to the market contract for the remainder of the block.

### Likelihood Explanation
`socialize-debt` is only invoked in bad-debt / loss-realization scenarios (deliberately rare, but exactly the moments where correct valuation matters most), and requires that the vault's index was already cached earlier in the same block — a common occurrence given that `accrue-and-cache` is called from multiple entry points (debt accrual folds, collateral accrual folds, price resolution) on almost every market transaction touching that asset. Any transaction landing in the same block after a socialize-debt event and before the block timestamp changes can trigger the stale read.

### Recommendation
Invalidate (or update) the `index-cache-` entry for `aid` inside `socialize-debt` (or have the market re-fetch/overwrite the cache immediately after calling `vault-socialize-debt`), so the cache can never diverge from vault state that changed outside of `accrue`.

### Proof of Concept
1. Within a block, a market operation (e.g. an accrual fold over collateral/debt lists) calls `accrue-and-cache(aid)` for a vault, caching `{index, lindex}` under `{timestamp: T, aid}`.
2. A liquidation transaction realizes bad debt on that vault and calls `vault-socialize-debt`, which sets a lower `lindex` in vault storage via `var-set lindex new-lindex` (`v0-vault-stx.clar` lines 956-966), bypassing the cache entirely.
3. Later in the same block (timestamp still `T`), any call to `accrue-and-cache(aid)` — from a borrow, health check, or another user's collateral valuation involving that vault's zToken — hits the cache and returns the pre-write-down `lindex` (`v0-4-market.clar` lines 249-251), overvaluing zToken collateral relative to the vault's true post-loss state. [1](#0-0) [2](#0-1) 

Note: I was not able to fully trace the exact statement order inside the market's `liquidate` public function within the available search budget, so the precise number of legitimate intervening operations between the cache-set and the `socialize-debt` call in a single liquidation transaction is unconfirmed; the cross-transaction (same-block) staleness path described above, however, is directly supported by the cited code and does not depend on that detail.

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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L944-967)
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
                       (mul-div-down current-lindex (- old-total-assets debt-reduction) old-total-assets)
                       u0)))

    (try! (check-caller-auth))
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

    (var-set lindex new-lindex)
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))

```
