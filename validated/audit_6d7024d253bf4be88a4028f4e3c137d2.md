### Title
`socialize-debt` uses a stale interest `index` without accruing first, causing lost/incorrect debt write-down - ([File: mainnet/contracts/vault/v0-vault-ststxbtc.clar])

### Summary
Every state-changing debt operation in the Zest vault contracts (`system-borrow`, `system-repay`, `deposit`, `redeem`, `transfer`, `flashloan`) begins by calling `(try! (accrue))` to roll the interest `index`/`lindex` forward to the current block before using them [1](#0-0) , but `socialize-debt` reads `index`, `principal-scaled`, `total-borrowed`, `assets`, and `lindex` directly via `var-get` without calling `accrue` first [2](#0-1) .

### Finding Description
`index` is a cached representation of accrued debt that is only brought current by `accrue`, which recomputes `next-index`/`next-liquidity-index` from elapsed time and writes them back with `var-set` [3](#0-2) . All other functions that mutate `principal-scaled`, `total-borrowed`, or `assets` based on debt math call `accrue` as their first binding in the `let`, e.g. `system-repay`:
```
(u (try! (accrue)))
(scaled-principal (var-get principal-scaled))
(idx (var-get index))
``` [4](#0-3) 

`socialize-debt`, however, omits this step entirely:
```
(define-public (socialize-debt (scaled-amount uint))
  (let ((scaled-principal (var-get principal-scaled))
        (borrowed (var-get total-borrowed))
        (idx (var-get index))
        ...
        (debt-reduction (mul-div-down scaled-amount idx INDEX-PRECISION))
        (principal-reduction (if (> scaled-principal u0)
                                (mul-div-down scaled-amount borrowed scaled-principal)
                                u0))
``` [2](#0-1) 

If interest has accrued since the last time any accrual-triggering function was called (i.e., time has passed on-chain with no intervening `deposit`/`redeem`/`borrow`/`repay`/`flashloan`), `idx` is stale/lower than the true current index. `debt-reduction` and the loss write-down (`new-lindex`) are computed from this stale index rather than the true accrued debt, and the stale `index`/`lindex` are never updated by `socialize-debt` itself, so the interest that should have accrued between the last accrual and this call is silently dropped from the ledger — it is neither minted to the treasury (as `accrue`'s `reserve-inc`/`treasury-lp` logic would do) nor reflected in the LP loss write-down. This is exactly the "cached value not invalidated when its source moves" pattern from the report (there, `originalAllocation` was zeroed without first updating rewards derived from it; here, `principal-scaled`/`total-borrowed`/`lindex` are mutated using a `index` value that was never rolled forward to reflect elapsed accrual).

### Impact Explanation
This affects LP share accounting: `lindex` determines the liquidity index used to compute total assets and shares for depositors (`total-assets`, `convert-to-assets-preview`, etc., used throughout `deposit`/`redeem`). Using a stale `index` in the bad-debt socialization means the write-down applied to `lindex` does not correctly reflect the actual accrued interest at the time of socialization, permanently misstating the value LPs are entitled to versus what was actually lost to bad debt. Depending on direction, LP depositors either absorb a mis-sized loss or fail to receive the correct reserve-fee mint that `accrue` would otherwise have produced — i.e., a permanent freezing/misallocation of unclaimed yield to the treasury/LPs, matching the in-scope "High" impact of permanent freezing of unclaimed yield.

### Likelihood Explanation
`socialize-debt` requires caller authorization (`check-caller-auth`), so it is gated similarly to `system-borrow`/`system-repay`, and it is invoked from the market contract during bad-debt socialization flows referenced in `mainnet/contracts/market/v0-4-market.clar`. The bug triggers deterministically whenever `socialize-debt` is called after any block-time gap without an intervening accrual-triggering call — a very common operational condition, since bad-debt socialization typically follows liquidations that may not themselves call `accrue` on this vault in the same transaction.

### Recommendation
Add `(u (try! (accrue)))` as the first binding in `socialize-debt`'s `let`, mirroring `system-borrow`/`system-repay`, so `index`/`lindex` are rolled forward before computing `debt-reduction`, `principal-reduction`, and `new-lindex`.

### Proof of Concept
1. Vault has outstanding scaled debt (`principal-scaled > 0`) and time passes with no `deposit`/`redeem`/`borrow`/`repay`/`flashloan` call, so `index` in storage is stale relative to `next-index()`.
2. Operator/market calls `socialize-debt(scaled-amount)` directly; the function reads `(var-get index)` (stale) instead of accruing first [5](#0-4) .
3. `debt-reduction` and the `lindex` write-down are computed from the stale `idx`, and `lindex`/`assets` are updated via `var-set` without ever running the `accrue` logic that would have minted reserve interest and advanced `index` [6](#0-5) .
4. The interest accrued between the last accrual and this call is permanently unaccounted for in both the treasury mint and the LP loss write-down.

### Citations

**File:** mainnet/contracts/vault/v0-vault-ststxbtc.clar (L835-866)
```text
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
                (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
                (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
                (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
                (treasury-lp (if (> reserve-inc u0) (mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc)) u0)))
            (if (not (is-eq idx next))
                (var-set index next)
                false)
            (if (not (is-eq lidx nliq))
                (var-set lindex nliq)
                false)
            (if (> treasury-lp u0)
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))

(define-public (system-borrow (amount uint) (receiver principal))
  (let (
```

**File:** mainnet/contracts/vault/v0-vault-ststxbtc.clar (L869-877)
```text
      (CAP-DEBT (var-get cap-debt))
      (available-assets (get-available-assets))
      (scaled-principal (var-get principal-scaled))
      (idx (var-get index))
      (debt (total-debt))
      (scaled-amount (mul-div-up amount INDEX-PRECISION idx))
      (updated-scaled-principal (+ scaled-principal scaled-amount)))

    (try! (check-caller-auth))
```

**File:** mainnet/contracts/vault/v0-vault-ststxbtc.clar (L902-908)
```text
(define-public (system-repay (amount uint))
  (let (
        (states (var-get pause-states))
        (u (try! (accrue)))
        (scaled-principal (var-get principal-scaled))
        (idx (var-get index))
        (debt (total-debt))
```

**File:** mainnet/contracts/vault/v0-vault-ststxbtc.clar (L944-958)
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
```

**File:** mainnet/contracts/vault/v0-vault-ststxbtc.clar (L963-966)
```text
    (var-set lindex new-lindex)
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))
```
