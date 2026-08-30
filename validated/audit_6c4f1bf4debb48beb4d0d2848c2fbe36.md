## Title
Rounding-based "dust sweep" in `liquidate` incorrectly zeroes out non-negligible remaining collateral/debt, causing free collateral seizure and premature bad-debt socialization - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
The `liquidate` function in `v0-4-market.clar` contains a dust-sweep mechanism structurally identical to GoodEntry's `addDust`: a "is this leftover amount negligible?" check is computed through a multi-step, multi-decimal, oracle-price-based conversion (USD normalize → penalty-adjust → raw token conversion → scaled-debt conversion → back to raw), and if that chain of truncating divisions rounds to zero, the code treats a potentially real, non-trivial remaining balance as pure dust and sweeps/writes it off entirely, instead of handling it proportionally.

### Finding Description
In `liquidate`, after computing the "proportional" collateral to seize (`coll-final-raw`), the code computes `coll-remaining` (leftover of the seized collateral asset) and decides whether that remainder is "dust": [1](#0-0) 

`remaining-debt-to-repay` is derived by chaining `normalize` (USD conversion, round down), `div-bps-down` (penalty adjustment), `mul-div-down` (USD→debt-token raw amount), `mul-div-down` (raw→scaled units, divided by the *current* accrued borrow index), then `mul-div-up` (scaled back to raw). If any of these truncating steps collapses the value to `0`, `coll-final` silently becomes the *entire* `user-coll-balance` for that asset rather than the previously computed proportional `coll-final-raw`: [2](#0-1) 

Crucially, `debt-to-repay` (the amount the liquidator actually pays) was already fixed earlier from `coll-final-raw`, not from the swept `coll-final`: [3](#0-2) 

So the liquidator can receive the full remaining collateral balance while paying only the debt amount computed against the smaller, proportional amount — exactly the same class of bug as GoodEntry's `addDust`, where a dust value calculated in one price/rounding domain (oracle-based notional maths) is used to gate a decision about actual token amounts in a different domain (raw/scaled debt-token units, affected by the accrued `borrow-index`), causing the "this is negligible" assumption to fail for non-negligible amounts.

The same pattern is repeated immediately after for the *entire remaining position*, not just the collateral asset being liquidated, via `other-debt-repayable`: [4](#0-3) 

If `other-debt-repayable` rounds to `0` (same class of compounding truncation, now additionally gated on `total-collateral-usd` vs `target-coll-full-usd` subtraction), `no-collateral-left` becomes `true` even though the borrower may still hold real value in other collateral assets that were never touched by this liquidation call. This feeds directly into bad-debt socialization: [5](#0-4) 

The root cause is the same as the external report: a "dust" threshold is derived through a chain of price/decimal/index-scaled conversions that do not correspond 1:1 to the actual quantity being gated (the true remaining collateral value vs. the true remaining scaled debt), so the check can be wrong in either direction — misclassifying a real, valuable remainder as zero.

### Impact Explanation
- If `remaining-debt-to-repay` incorrectly evaluates to `0` for a collateral remainder that is not actually negligible (e.g., due to `rem-borrow-index` having grown substantially from accrued interest, making the `mul-div-down ... INDEX-PRECISION rem-borrow-index` step round the scaled amount to zero even though the raw token amount was non-zero), the liquidator is granted the borrower's *entire* remaining balance of that collateral asset for free (no incremental debt repayment), directly stealing funds at rest from the borrower. This lands on **Critical — direct theft of user funds at rest**.
- If `other-debt-repayable` incorrectly evaluates to `0` while the borrower still holds real collateral in other assets, `no-collateral-left` becomes true and the protocol prematurely socializes the borrower's remaining debt as bad debt across the vault, even though uncaptured collateral value still exists with the borrower. This directly writes off debt that is not actually uncollateralized, causing **protocol insolvency** (Critical).

### Likelihood Explanation
This requires no external actor collusion and no oracle manipulation by the attacker — only a liquidator choosing a `debt-amount` that lands the proportional math near a rounding boundary for a specific collateral/debt asset-decimal combination (e.g., low-decimal, high-price debt assets, or positions where `rem-borrow-index`/`INDEX-PRECISION` has drifted from 1 due to accrued interest). Because the liquidator fully controls `debt-amount` (within the max-liquidatable cap) and `collateral-ft`/`debt-ft` choice, they can search for/target the parameter combination that triggers the zero-rounding branch, making this reachable by any liquidator without special privileges.

### Recommendation
Do not use a re-derived, multi-step, differently-rounded USD/price conversion to decide whether a raw token remainder is "dust." Instead:
1. Compare the actual remaining raw collateral/debt amounts directly against a fixed, small, protocol-defined minimum unit (in the same units/decimals already used for the proportional calculation), rather than round-tripping through USD and scaled-debt-index space.
2. Ensure the check that gates sweeping full `user-coll-balance` (or triggering `no-collateral-left`) uses the exact same rounding direction and unit domain as `debt-to-repay`, so that whatever collateral is swept always has a corresponding, correctly computed debt repayment amount.
3. Add invariant tests fuzzing decimals/prices/borrow-index growth to ensure `remaining-debt-to-repay`/`other-debt-repayable` never round to zero for meaningfully-valued remainders.

### Proof of Concept
1. Borrower opens a position with collateral asset `C` (e.g., 8-decimal, high oracle price) and debt asset `D` such that the vault's `borrow-index` for `D` has grown well above `INDEX-PRECISION` due to accrued interest (achievable simply by letting time pass on a real position, or by choosing an asset with high interest rate).
2. Borrower's LTV crosses `ltv-liq-partial`, making them liquidatable.
3. Liquidator calls `liquidate` with a `debt-amount` chosen so the proportional collateral calc (`scale-debt-for-liquidation`) leaves `coll-remaining` at a level whose USD-equivalent, once run through `normalize → div-bps-down → mul-div-down (raw) → mul-div-down (÷ rem-borrow-index) → mul-div-up`, rounds to `0` scaled units — even though the raw `rem-debt-tokens` value was non-zero (lines 1477-1485 of `v0-4-market.clar`).
4. `coll-final` is set to `user-coll-balance` (full remaining balance of asset `C`) at line 1486, while `debt-to-repay` (fixed earlier at line 1474) reflects only the smaller, proportional amount.
5. Liquidation executes: liquidator pays `debt-to-repay` (proportional) but receives the borrower's entire `C` balance — collateral value beyond the intended liquidation bonus is stolen from the borrower with no counterparty payment. [6](#0-5)

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L1470-1475)
```text
    ;; debt scaling for storage
    (curr-scaled (get-account-scaled-debt borrower debt-aid))
    (scaled-info (scale-debt-for-liquidation debt-final coll-actual curr-scaled debt-aid))
    (scaled-to-remove (get scaled-to-remove scaled-info))
    (debt-to-repay (get debt-to-repay scaled-info))
    (coll-final-raw (get coll-final scaled-info))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1476-1493)
```text
    (coll-remaining (- user-coll-balance coll-final-raw))
    (remaining-debt-to-repay
      (if (> coll-remaining u0)
        (let ((rem-coll-usd (normalize (* coll-remaining coll-price) coll-decimals false))
              (rem-debt-usd (div-bps-down rem-coll-usd (+ BPS liq-penalty-max)))
              (rem-debt-tokens (mul-div-down rem-debt-usd (pow u10 debt-decimals) debt-price))
              (rem-borrow-index (get index (unwrap-panic (get-cached-indexes debt-aid))))
              (rem-scaled (mul-div-down rem-debt-tokens INDEX-PRECISION rem-borrow-index)))
          (mul-div-up rem-scaled rem-borrow-index INDEX-PRECISION))
        u1))
    (coll-final (if (is-eq remaining-debt-to-repay u0) user-coll-balance coll-final-raw)))

    (asserts! (not (is-liquidation-paused debt-aid)) ERR-LIQUIDATION-PAUSED)
    (asserts! (is-eq contract-caller tx-sender) ERR-AUTHORIZATION)
    (asserts! (> debt-amount u0) ERR-AMOUNT-ZERO)
    (asserts! (> debt-to-repay u0) ERR-ZERO-LIQUIDATION-AMOUNTS)
    (asserts! (> coll-final u0) ERR-ZERO-LIQUIDATION-AMOUNTS)
    (asserts! (>= coll-final min-collateral-expected) ERR-SLIPPAGE)
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1514-1532)
```text
          (target-coll-full-usd (normalize (* user-coll-balance coll-price) coll-decimals false))
          (other-coll-usd (if (> total-collateral-usd target-coll-full-usd)
                              (- total-collateral-usd target-coll-full-usd)
                              u0))
          (other-debt-repayable
            (if (> other-coll-usd u0)
              (let ((other-adj (div-bps-down other-coll-usd (+ BPS liq-penalty-max)))
                    (other-tokens (mul-div-down other-adj (pow u10 debt-decimals) debt-price))
                    (other-borrow-idx (get index (unwrap-panic (get-cached-indexes debt-aid))))
                    (other-scaled (mul-div-down other-tokens INDEX-PRECISION other-borrow-idx)))
                (mul-div-up other-scaled other-borrow-idx INDEX-PRECISION))
              u0))
          (no-collateral-left (and
                                (is-eq coll-removed u0)
                                (or
                                  (is-eq (len (get collateral pos-full)) u1)
                                  (and
                                    (is-eq (len (get collateral pos-full)) (len (get collateral position)))
                                    (is-eq other-debt-repayable u0))))))
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
