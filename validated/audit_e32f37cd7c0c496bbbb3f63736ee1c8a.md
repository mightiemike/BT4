### Title
Stale market-level index cache after bad-debt socialization enables mispriced zToken collateral within the same block - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`market.clar`'s `index-cache` stores each vault's `{index, lindex}` keyed only by `{timestamp: stacks-block-time, aid}` [1](#0-0) . Once populated for a given block timestamp, `accrue-and-cache` returns the cached tuple for every subsequent call in that same block without re-checking the vault's actual state [1](#0-0) . However, `liquidate` can, within that same block, trigger `socialize-debt-asset` → vault `socialize-debt`, which directly mutates the vault's `lindex` down-write in the vault contract when bad debt is written off [2](#0-1) . This vault-side mutation does not go through `accrue-and-cache`, so it never invalidates the market's already-populated `index-cache` entry for that vault/timestamp. Any later transaction in the same block that reads zToken/collateral prices via `resolve-ztoken`/`get-cached-indexes` will use the pre-socialization (stale, too-high) `lindex`, overvaluing that vault's zToken as collateral [3](#0-2) .

### Finding Description
The market maintains a per-block cache of vault indexes to save cross-contract calls:
```
(define-private (accrue-and-cache (aid uint))
  (let ((cache-key { timestamp: stacks-block-time, aid: aid })
        (cached? (map-get? index-cache cache-key)))
    (match cached?
      cached-indexes (ok cached-indexes)
      (let ((indexes (try! (vault-accrue aid))))
        (map-set index-cache cache-key indexes)
        (ok indexes)))))
``` [1](#0-0) 

This cache is the single source of truth used to price zToken collateral via `resolve-ztoken`, which multiplies price by the cached `lindex`:
```
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
``` [3](#0-2) 

The vault's actual `lindex` value can change independently of `accrue-and-cache` in `liquidate`, when a liquidation leaves a borrower with no collateral and bad debt must be socialized. The market folds `socialize-debt-asset` over the residual debt list, which calls the vault's `socialize-debt` [4](#0-3) . Inside the vault, `socialize-debt` directly writes a reduced `lindex` proportional to the loss, bypassing the normal `accrue()`/cache path entirely:
```
(define-public (socialize-debt (scaled-amount uint))
  (let ((scaled-principal (var-get principal-scaled))
        ...
        (current-lindex (var-get lindex))
        (old-total-assets (total-assets))
        ...
        (new-lindex (if (and (> old-total-assets u0) (> old-total-assets debt-reduction))
``` [2](#0-1) 

Because this write happens inside the vault contract, not through `market.clar`'s `accrue-and-cache`, the market's `index-cache` map entry for `{timestamp: stacks-block-time, aid: vault-id}` — if it was already populated earlier in the same block (e.g., by the liquidator's own `accrue-user-collateral` call at the start of `liquidate`, or by any other user's transaction earlier in the block) — is never overwritten. Every subsequent read of `get-cached-indexes` for that vault in the remainder of the block returns the pre-socialization, too-high `lindex`.

### Impact Explanation
Any operation in the same block that follows a bad-debt socialization event and reads the affected vault's zToken price (borrow, collateral-add capacity checks, further liquidations) will overvalue that vault's zToken as collateral, since `resolve-ztoken` multiplies the oracle price by the stale, inflated `lindex`. This lets users borrow more than their real collateral supports, or block liquidators from liquidating positions that are actually unhealthy under the corrected (lower) `lindex`, temporarily freezing funds / letting under-collateralized positions accrue further, and undermining the accounting used for solvency checks across the protocol for the remainder of the block. This lands in the in-scope **temporary freezing of funds** / mispriced-collateral impact class, matching the analog bug class (cached TVL/price component not invalidated when its underlying source value moves).

### Likelihood Explanation
Bad-debt socialization only occurs when a liquidation leaves a borrower with zero remaining collateral, which is a normal, expected liquidation-engine event, not a rare edge case. Since Stacks blocks routinely contain multiple transactions, any borrow/collateral-add/liquidate transaction that lands in the same block after a socialization event and touches the same vault's zToken would observe the stale cache. Likelihood is Medium: it requires the ordering of a socialization event followed by a same-block operation on the same vault's zToken, which is plausible but not guaranteed on every block.

### Recommendation
When `socialize-debt` (or any other vault function that mutates `index`/`lindex` outside of the normal `accrue-and-cache` call path) is invoked from `market.clar`, force-refresh the market's `index-cache` entry for that `aid` immediately after the vault call returns (e.g., re-call `vault-accrue`/read the vault's now-current indexes and `map-set` the cache), or invalidate the cache entry so the next `accrue-and-cache` call is forced to re-fetch from the vault instead of serving the stale value for the rest of the block.

### Proof of Concept
Conceptual sequence within a single block (block timestamp `T`):
1. User A calls `collateral-add` using zSTSTX collateral; this calls `accrue-user-collateral` → `accrue-and-cache(stSTX)`, populating `index-cache{T, stSTX} = {index, lindex=L0}`.
2. A liquidator calls `liquidate` on some fully-underwater stSTX borrower with zero remaining collateral; `no-collateral-left` is true, so `socialize-debt-asset` calls vault `socialize-debt`, which reduces the vault's actual `lindex` to `L1 < L0` to reflect the loss.
3. Because this write bypasses `accrue-and-cache`, `index-cache{T, stSTX}` in `market.clar` still holds the old `L0`.
4. Within the same block, User B calls `borrow` or `collateral-add` using zstSTX collateral; `resolve-ztoken` reads `get-cached-indexes(stSTX)`, obtaining the stale `L0` instead of the corrected `L1`, and values User B's zstSTX collateral higher than it actually is, allowing an unsafe borrow to pass the health check that would have failed under `L1`.

Note: I was unable to fully trace whether other call paths in `liquidate`/`socialize-debt-asset` might incidentally refresh the cache for the same `aid` in the same transaction (e.g., through a subsequent `accrue-and-cache` call on the *liquidator's* own position touching the same vault before the socialization writes `lindex`), so exploitability specifically requires that the cache is populated *before* socialization and read *after* it, by a different transaction later in the same block — I could not execute a live test to confirm this ordering is reachable on-chain versus purely within a single atomic transaction (Clarity read-after-write on `var-get lindex` inside one transaction would actually see the updated value if re-read via a fresh `vault-accrue` call, but the market-level `index-cache` map is what is stale, not the vault's own state).

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

**File:** mainnet/contracts/market/v0-4-market.clar (L343-347)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1534-1560)
```text
      ;; Handle bad debt socialization if no collateral left
      (let ((bad-debt-socialized 
              (if no-collateral-left
                  (let ((stripped-debt-list (filter-out-debt-asset (get debt pos-full) debt-aid))
                        (fresh-debt-list (if (is-eq debt-updated u0)
                                             stripped-debt-list
                                             (unwrap-panic (as-max-len?
                                               (append stripped-debt-list
                                                       { aid: debt-aid, scaled: debt-updated })
                                               u64)))))
                    (if (> (len fresh-debt-list) u0) ;; if still has debt
                      (let ((socialization-result (fold socialize-debt-asset 
                                                        fresh-debt-list 
                                                        { borrower: borrower, success: true })))
                        (asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)
                        ;; emit bad-debt-socialized event
                        (print {
                          action: "bad-debt-socialized",
                          caller: contract-caller,
                          data: {
                            borrower: borrower,
                            debt-list: fresh-debt-list
                          }
                        })
                        true)
                      false))
                  false)))
```

**File:** local-testing/contracts/vault/vault-ststx.clar (L948-960)
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
