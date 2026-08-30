### Title
`socialize-debt` Writes Down The Liquidity Index Using A Stale Borrow Index For Debt Assets Not Accrued In The Same Liquidation Transaction - (File: `local-testing/contracts/vault/vault-stx.clar` / `mainnet/contracts/vault/v0-vault-stx.clar` and sibling vault contracts)

### Summary
`socialize-debt` in every vault contract computes the bad-debt write-down using `(var-get index)` directly, without first calling `accrue()` to bring that index up to the current block timestamp. Every other state-mutating vault entry point (`system-borrow`, `system-repay`, `flashloan`) explicitly calls `(try! (accrue))` before reading `index`/`lindex`. When `market.clar`'s liquidation flow socializes bad debt for debt assets *other than* the one just repaid via `vault-system-repay`, those vaults never receive an `accrue()` call in the same transaction, so `socialize-debt` operates on a stale index — a cached value whose source (elapsed interest) has moved without invalidation.

### Finding Description
`socialize-debt` reads the vault's current `index`, `lindex`, `principal-scaled`, `total-borrowed` and `assets`, then computes: [1](#0-0) 

`debt-reduction` is `mul-div-down scaled-amount idx INDEX-PRECISION`, and `new-lindex` writes down the liquidity index proportionally to `old-total-assets` (itself derived from `total-debt()`, which also uses the stale `var-get index` through `calc-cumulative-debt`). Unlike `system-borrow`/`system-repay`/`flashloan`, which all begin with `(u (try! (accrue)))` to refresh `index`/`lindex` to `stacks-block-time` before using them: [2](#0-1) 

`socialize-debt` has no such call: [3](#0-2) 

In `market.clar`'s `liquidate` flow, bad-debt socialization is performed via a `fold` over `fresh-debt-list`, which is built by stripping out only the currently-repaid `debt-aid` and (conditionally) re-appending its updated scaled amount — any *other* debt assets the borrower holds remain in the list and get socialized through `socialize-debt-asset` without any prior `vault-system-repay` (and therefore no `accrue()`) having been invoked on those vaults in the same transaction: [4](#0-3) 

The only mechanism that could have refreshed those other vaults' indexes is `accrue-and-cache`/`get-cached-indexes`, which is a market-level, timestamp-keyed cache — but it is only populated for assets that were explicitly accrued via `accrue-user-debts`/`accrue-user-collateral` earlier in the transaction, and `socialize-debt` itself never consults or seeds that cache; it reads the vault's raw storage variable directly: [5](#0-4) 

Thus, if the elapsed time since that other vault's `last-update` is significant, `debt-reduction`, `principal-reduction` and the `lindex` write-down are all computed against an understated debt index, permanently corrupting the vault's `principal-scaled`, `total-borrowed`, `assets` and `lindex` state relative to their true economic values.

### Impact Explanation
The liquidity index (`lindex`) directly determines the redemption value of that vault's zft (share) holders. Because the write-down is applied using a stale borrow index, the recorded loss allocated to zft holders does not match the real loss, permanently misstating the redeemable value of deposits for all suppliers of that vault. This is a permanent freezing/misallocation of unclaimed yield/principal value for LP holders of the vault whose debt is socialized without accrual, landing in the "permanent freezing of funds" impact category.

### Likelihood Explanation
This triggers whenever a borrower liquidated has debt positions in more than one vault and the liquidation's final collateral seizure fully wipes out collateral (`no-collateral-left` true), forcing socialization of debt assets beyond the one being repaid — a realistic and unprivileged scenario for any multi-asset borrower undergoing full liquidation, requiring no DAO action, no oracle manipulation and no attacker-controlled interference between users.

### Recommendation
Call `(try! (accrue))` at the start of `socialize-debt` in every vault contract before reading `index`/`lindex`/`principal-scaled`/`total-borrowed`/`assets`, mirroring the pattern already used in `system-borrow`, `system-repay`, and `flashloan`, so the write-down is always computed against the index accrued to the current block timestamp.

### Proof of Concept
1. Borrower has debt in vault-A (small amount) and vault-B (large amount, last accrued long ago), with a single collateral asset.
2. Liquidator calls `market.liquidate` targeting vault-A's debt; the transaction only calls `vault-system-repay` for vault-A (which internally calls `accrue()` for vault-A), never touching vault-B.
3. Collateral is fully consumed, `no-collateral-left` is `true`, so `fresh-debt-list` includes vault-B's remaining scaled debt.
4. `fold socialize-debt-asset fresh-debt-list` invokes vault-B's `socialize-debt`, which reads `(var-get index)` for vault-B — a value never refreshed in this transaction and stale relative to `stacks-block-time`.
5. `debt-reduction`/`new-lindex` are computed with the stale index, permanently corrupting vault-B's `lindex`, `principal-scaled`, and `total-borrowed`, misstating redemption value for all vault-B zft holders going forward. [3](#0-2) [4](#0-3)

### Citations

**File:** local-testing/contracts/vault/vault-stx.clar (L865-868)
```text
(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
```

**File:** local-testing/contracts/vault/vault-stx.clar (L944-962)
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

```

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

**File:** local-testing/contracts/market/market.clar (L1557-1583)
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
