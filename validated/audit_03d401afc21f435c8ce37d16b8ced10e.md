### Title
Stale `index-cache-` entry not invalidated when `socialize-debt` writes down a vault's `lindex` mid-transaction, causing subsequent same-block liquidations to price zTokens incorrectly - ([File: local-testing/contracts/market/market.clar])

### Summary
`market.clar` caches each vault's `(index, lindex)` per `{timestamp, aid}` the first time a transaction touches that asset, and every later price/health lookup in the same block reuses that cached value instead of re-reading the vault. `liquidate()` can trigger `socialize-debt`, which mutates the vault's `lindex` directly (bypassing the cache) when a borrower's collateral is fully exhausted. When `liquidate-multi` batches several `liquidate()` calls in one transaction, an earlier position's bad-debt socialization silently write-downs a vault's `lindex`, but the stale, higher `lindex` remains cached and is reused by later positions in the same batch that reference the same asset as collateral or debt, mispricing zTokens for the rest of the transaction.

### Finding Description
`accrue-and-cache` is a memoized wrapper around `vault-accrue`, keyed only by `{timestamp: stacks-block-time, aid}`: [1](#0-0) 

Because `stacks-block-time` is constant for the whole transaction/block, once a vault's `(index, lindex)` is cached it is treated as authoritative for every subsequent read of that `aid` in the same transaction, no matter what else happens to the vault in between.

`resolve-ztoken`, used to price zToken collateral/debt for health checks and liquidation, reads directly from this cache rather than the vault: [2](#0-1) 

Separately, `socialize-debt` on the vault contract writes a **new, lower** `lindex` directly to vault storage when bad debt must be absorbed by remaining suppliers — this is a genuine change to the vault's underlying value, independent of and without going through `accrue-and-cache`: [3](#0-2) 

`liquidate()` invokes this path when a borrower has no collateral left for a debt asset, folding `socialize-debt-asset` over the borrower's remaining debt list and calling `vault-socialize-debt`: [4](#0-3) 

`liquidate-multi` executes several `liquidate()` calls (each potentially triggering `socialize-debt`) within a single transaction via `(map call-liquidate positions)`: [5](#0-4) 

The root cause: `accrue-and-cache`'s cache-hit path returns the previously stored `{index, lindex}` unconditionally, and nothing invalidates or refreshes that entry when `socialize-debt` mutates the vault's `lindex` mid-transaction. This is exactly the "cached value not invalidated when its source moves" pattern — analogous to the Centrifuge `SimplePriceManager`/`BatchRequestManager#revokeShares()` issue, where a share-supply-affecting operation (`revoke`) updated on-chain state without updating the cached NAV/price components consumed by later reads in the same synchronization window.

### Impact Explanation
`socialize-debt` writes the vault's `lindex` DOWN (loss write-down), but the market's cache retains the OLD, higher `lindex`. Any position processed later in the same `liquidate-multi` transaction that references the same asset:
- as **collateral**: its zToken collateral is overvalued (stale high `lindex` inflates `resolve-ztoken`'s price), making the position appear healthier than it truly is. This can let a genuinely liquidatable/undercollateralized position be skipped, or reduce the computed liquidation-penalty/collateral-seized amount, leaving the protocol/other suppliers with a bigger loss than they should absorb (temporary/permanent freezing of other users' funds via increased future bad debt).
- as **debt**: the debt is likewise overvalued, letting a liquidator repay less in real terms for a given seized-collateral amount, or seize more collateral per unit of debt repaid than the true (post-write-down) price justifies — a direct value transfer out of the protocol/borrower's remaining collateral to the liquidator.

Both outcomes match the in-scope impact classes (temporary freezing of funds / theft of user funds), since the health/pricing computation feeding collateral seizure and bad-debt accounting is provably wrong for the remainder of the batched transaction.

### Likelihood Explanation
This requires: (1) a `liquidate-multi` call with multiple positions sharing a common asset (as collateral in one position, debt in another, or the same asset in different roles across positions) and (2) at least one earlier position in the batch triggering `socialize-debt` (i.e., the borrower has no collateral left after liquidation on that asset). Both preconditions are realistic and unprivileged — anyone can call `liquidate-multi` with any position list, and severely undercollateralized/insolvent borrowers whose liquidation triggers socialization are an expected, not exotic, protocol state. No governance or oracle manipulation is required.

### Recommendation
Invalidate or refresh the `index-cache-` entry for an asset whenever `socialize-debt` (or any other vault call that changes `lindex`/`index` outside of `vault-accrue`) is executed within the same transaction — e.g., have `vault-socialize-debt` return the new `(index, lindex)` and have `market.clar` `map-set` the cache immediately after calling it, or drop the cache entry for that `aid` so the next `accrue-and-cache` call is forced to re-read the vault.

### Proof of Concept
1. Borrower A has debt in vault X and collateral fully in a zToken of asset X (or another zToken), positioned so that after liquidation, `no-collateral-left` is true and `socialize-debt-asset` is invoked, calling `vault-socialize-debt` on vault X, which lowers X's `lindex` (see `socialize-debt` in `vault-stx.clar`, lines 944-967).
2. Borrower B, in the same `liquidate-multi` batch (submitted after A in the `positions` list), holds zToken-X as collateral or debt.
3. When `liquidate()` runs for B, `accrue-and-cache` for `aid = X` finds a cache HIT from the earlier accrual done for A's liquidation (same `stacks-block-time`), returning the stale, pre-write-down `lindex` (`local-testing/contracts/market/market.clar` lines 253-265).
4. `resolve-ztoken` uses this stale `lindex` to price B's zToken-X collateral/debt (lines 365-369), producing an inflated valuation that does not reflect the true, just-written-down vault state.
5. B's liquidation health check, liquidation percentage, and collateral-seized/debt-repaid amounts are computed against this incorrect price, resulting in under-collateralization of the liquidation outcome relative to the vault's actual (post-socialization) backing.

### Citations

**File:** local-testing/contracts/market/market.clar (L253-265)
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

**File:** local-testing/contracts/market/market.clar (L365-369)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```

**File:** local-testing/contracts/market/market.clar (L1556-1583)
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

**File:** local-testing/contracts/vault/vault-stx.clar (L944-967)
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1593-1599)
```text
(define-public (liquidate-multi
                (positions (list 64 { borrower: principal,
                                      collateral-ft: <ft-trait>,
                                      debt-ft: <ft-trait>,
                                      debt-amount: uint,
                                      min-collateral-expected: uint })))
  (ok (map call-liquidate positions)))
```
