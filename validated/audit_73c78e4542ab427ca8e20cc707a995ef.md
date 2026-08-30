### Title
Unchecked subtraction in vault `accrue()` reserve calculation can strand all vault operations - (File: `mainnet/contracts/vault/v0-vault-stx.clar` and equivalent `v0-vault-*.clar` files)

### Summary
Every Zest v2 vault contract (`v0-vault-stx.clar`, `v0-vault-usdc.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdh.clar`) computes the treasury's protocol-fee share inside `accrue()` using an unchecked subtraction between two independently-derived quantities, `total-assets-preview()` and `reserve-inc`. If `reserve-inc` ever equals or exceeds `total-assets-preview()`, the subtraction underflows a `uint` and the whole `accrue()` call reverts. Because `accrue()` is the first step (`(u (try! (accrue)))`) of every vault entry point (`deposit`, `redeem`, `system-borrow`, `system-repay`, `flashloan`), a revert here would strand pending deposits/withdrawals/borrows/repayments in exactly the same way the reported `Escrow.processPayment()` underflow stranded escrowed funds by combining independently-set values (`serverAmount`, `feeForGasUsdt`, `feeForSidekick`) without validating the total against the bound (`transaction.amount`).

### Finding Description
In `accrue()`:
```
mainnet/contracts/vault/v0-vault-stx.clar:843-854
(let ((next (next-index))
      (nliq (next-liquidity-index))
      (scaled-principal (var-get principal-scaled))
      (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
      (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
      (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
      (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
      (treasury-lp (if (> reserve-inc u0) (mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc)) u0)))
``` [1](#0-0) 

`reserve-inc` is derived from `debt-delta` (interest accrued since the last `accrue()` call, itself a function of how much time/index growth has elapsed) and the governance-set `fee-reserve` bps parameter, while `total-assets-preview()` reflects the vault's current total assets (idle liquidity + outstanding debt). These two quantities are set/updated independently — exactly the same shape as the reported bug, where `serverAmount` (set at transaction creation), `feeForGasUsdt` (set by admin), and `feeForSidekick` (computed as a percentage) are combined without validating that their sum stays within the bound (`transaction.amount`). Here, nothing validates that `reserve-inc < total-assets-preview()` before the subtraction `(- (total-assets-preview) reserve-inc)` is evaluated.

The same unguarded pattern additionally shows a second instance of the class in `system-repay()`, computing `interest-paid (- capped-amount principal-repaid)`; however, that path preserves the invariant `total-borrowed <= debt`, so `principal-repaid <= capped-amount` and no reachable underflow occurs there. The `accrue()` reserve calculation has no analogous invariant expressed anywhere in the visible code, and `debt-delta` can grow arbitrarily large the longer `accrue()` is skipped (each vault also has an explicit "pause accrue" pass-through path that lets time elapse without indexing, per the "PAUSED: Pass-through without reverting" branch), while `total-assets-preview()` is bounded by whatever liquidity/collateral currently sits in the vault. [2](#0-1) 

This is precisely the "unchecked arithmetic" bug class from the report — a subtraction between values controlled by different levers (accrual time, a governance bps parameter, and market-wide asset balances) without a bound check — but here it sits in a function that every deposit, redeem, borrow, and repay depends on via `(try! (accrue))`. [3](#0-2) 

### Impact Explanation
Because `accrue()` is invoked at the top of `deposit`, `redeem`, `system-borrow`, `system-repay`, and `flashloan` in every one of the six vaults, an underflowing subtraction here reverts the entire outer transaction, not just the fee bookkeeping. Any pending deposit, withdrawal, borrow, or repay attempted after the underflow condition is reached fails deterministically until the underlying state (e.g., `fee-reserve`, or the elapsed un-accrued interest) changes. This is a temporary (potentially indefinite, if nothing forces `debt-delta`/`fee-reserve` back below the threshold) freezing-of-funds condition across the affected vault, matching the in-scope "temporary freezing of funds" impact class. [4](#0-3) 

### Likelihood Explanation
Reaching the underflow requires `reserve-inc >= total-assets-preview()`. `reserve-inc` is a bps-scaled share of `debt-delta`, and `debt-delta` grows unboundedly with elapsed un-accrued time (interest compounds against `next-index`), while `total-assets-preview()` is limited by whatever liquidity the vault currently holds. The vault explicitly supports a pause mode where `accrue()` is skipped entirely ("PAUSED: Pass-through without reverting"), which lets un-accrued time build up; once accrual resumes, the catch-up `debt-delta` (and thus `reserve-inc`) can be disproportionately large relative to a vault's current `total-assets-preview()`, especially for a vault with low deposited liquidity relative to its outstanding scaled debt. I was not able to fully inspect the definition of `total-assets-preview()` within the available index to derive an exact numeric bound, so the precise threshold at which this triggers is not fully proven here — this should be verified directly against the vault source before treating likelihood as high-confidence. [5](#0-4) 

### Recommendation
Cap `reserve-inc` at `total-assets-preview()` (or skip minting `treasury-lp` and log for later distribution) before computing `treasury-lp`, e.g.:
```
(safe-reserve-inc (min reserve-inc (- (total-assets-preview) u1)))
```
or explicitly `asserts!` that `reserve-inc < total-assets-preview()` and fall back to `u0` treasury-lp in the failing case, so `accrue()` — and everything that depends on it — can never revert due to this calculation.

### Proof of Concept
1. Vault's `pause-states` has `accrue` paused (deliberate pass-through path), or the vault otherwise goes a long interval without anyone calling any vault entry point.
2. `principal-scaled` debt compounds against `next-index()` over that interval, so when `accrue()` is finally invoked (triggered by the next `deposit`/`redeem`/`system-borrow`/`system-repay`), `debt-delta = new-debt - old-debt` reflects the full un-accrued interest catch-up and can be large relative to the vault's current holdings.
3. `reserve-inc = mul-div-down(debt-delta, fee-reserve, BPS)` scales with `debt-delta`; if the vault's `total-assets-preview()` (idle liquidity + tracked debt) is smaller than `reserve-inc` at that moment (e.g., a vault with low deposited liquidity relative to accrued interest), the expression `(- (total-assets-preview) reserve-inc)` underflows.
4. The underflow causes `accrue()` to revert, which reverts the outer `deposit`/`redeem`/`borrow`/`repay`/`flashloan` call, exactly like the reported `processPayment()` revert — except here every subsequent call to any vault entry point fails the same way until the imbalance is resolved, freezing the vault. [3](#0-2)

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L835-900)
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
      (states (var-get pause-states))
      (u (try! (accrue)))
      (CAP-DEBT (var-get cap-debt))
      (available-assets (get-available-assets))
      (scaled-principal (var-get principal-scaled))
      (idx (var-get index))
      (debt (total-debt))
      (scaled-amount (mul-div-up amount INDEX-PRECISION idx))
      (updated-scaled-principal (+ scaled-principal scaled-amount)))

    (try! (check-caller-auth))
    (asserts! (not (get borrow states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (<= amount available-assets) ERR-INSUFFICIENT-VAULT-LIQUIDITY)
    (asserts! (<= (+ debt amount) CAP-DEBT) ERR-DEBT-CAP-EXCEEDED)

    (var-set principal-scaled updated-scaled-principal)
    (var-set total-borrowed (+ (var-get total-borrowed) amount))
    (try! (send-underlying amount receiver))

    (print {
      action: "system-borrow",
      caller: contract-caller,
      data: {
        receiver: receiver,
        amount: amount,
        scaled-amount: scaled-amount,
        principal-scaled: updated-scaled-principal,
        total-borrowed: (var-get total-borrowed),
        index: idx
      }
    })

    (ok true)))
```
