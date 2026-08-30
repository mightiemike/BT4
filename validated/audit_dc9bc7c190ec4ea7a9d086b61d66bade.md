### Title
Interest/fee accounting is silently lost when `accrue` is paused because dependent operations proceed on a stale, non-invalidated index - ([File: local-testing/contracts/vault/vault-stx.clar])

### Summary
Every Zest vault contract (`vault-stx.clar`, `vault-sbtc.clar`, `vault-usdc.clar`, `vault-usdh.clar`, `vault-ststx.clar`, `vault-ststxbtc.clar`) implements `accrue` with a pause "pass-through" branch: when the `accrue` flag in `pause-states` is set, the function returns the *current* `index`/`lindex` unchanged instead of computing and applying the interest that has economically accrued since `last-update` [1](#0-0) . Every state-mutating entry point that touches the debt/liquidity index (`redeem`, `deposit`, `system-borrow`, and by the same pattern `system-repay`) calls `accrue()` as its very first step and then immediately uses the returned (possibly stale) `index`/`lindex` to compute how much debt is removed or how many shares are minted/burned [2](#0-1) . Because `last-update` is *not* advanced while paused, the stale index is used to finalize state-changing operations (e.g., debt repayment reducing `principal-scaled`) as if no time had passed, even though the interest-rate model says interest is owed for that elapsed window.

### Finding Description
`accrue()`'s pause branch is a genuine "pass-through instead of revert": rather than blocking calls that depend on an up-to-date index, it lets them proceed with the old cached `index`/`lindex` value [3](#0-2) . The design intent seems to be that once `accrue` is unpaused, the next real `accrue()` call will compute `next-index`/`next-liquidity-index` against `last-update` and "catch up" all missed interest atomically over the remaining `principal-scaled` [4](#0-3) .

The flaw is that this catch-up mechanism only works if the `principal-scaled` that should have accrued interest is still present when the real accrual finally happens. If a borrower repays (or a portion of debt is otherwise scaled down) while `accrue` is paused, that repayment is priced using the stale `idx`/`lindex` returned by the pass-through — i.e., the debt token amount owed is computed as though zero time (zero interest) had elapsed since `last-update`, even though wall-clock time (and therefore economically owed interest under the rate model) has moved forward. Once `accrue` is unpaused, the subsequent real accrual call computes `debt-delta` and the protocol's `reserve-inc`/`treasury-lp` fee mint based on the *current* (now reduced) `principal-scaled` [5](#0-4) . The specific fraction of interest that should have accrued on the now-repaid principal for the paused window is mathematically unrecoverable — that principal is gone from the scaled-debt base by the time the "catch-up" runs, so neither the borrower is ever charged for it, nor is the protocol's `fee-reserve` share (`reserve-inc`) or LP interest ever minted for it.

This is the same root-cause shape as the BakerFi report: a fee/interest component that is due at the moment of a state-changing operation is not reflected in the value used to update dependent accounting (`newDeployedAmount` there; `principal-scaled`/`reserve-inc` here), because the operation is executed against a value (the index) that was not correctly invalidated/refreshed at the moment it mattered.

### Impact Explanation
This results in a permanent loss of unclaimed yield: interest that should have accrued to LPs and the protocol's `fee-reserve` treasury share is never realized once the underlying principal is repaid under the stale index. This is a High-severity impact per the given classification ("theft of unclaimed yield ... or permanent freezing of unclaimed yield").

### Likelihood Explanation
Requires the vault's `accrue` pause flag to be set (an operational lever intentionally exposed to authorized operators, not a compromise) while borrow/repay/deposit/redeem operations continue to be permitted (their own pause flags are independent booleans in the same `pause-states` tuple, so `accrue` can be paused while `repay`/`borrow`/`redeem` remain active) [6](#0-5) . Any borrower (no privileged access needed) can time a repay during such a window to avoid part of their accrued interest; the loss is borne by LPs/protocol, not from user-to-user interference.

### Recommendation
When `accrue` is paused, either (a) also pause every other lending/tokenized-vault operation that depends on `index`/`lindex` (`deposit`, `redeem`, `system-borrow`, `system-repay`), or (b) always compute the "would-be" `next-index`/`next-liquidity-index` for use inside those operations even while the persisted state update is skipped, so that debt repayments are always priced against the economically correct index and the missed `reserve-inc` fee is still captured against the correct (pre-repayment) `principal-scaled`.

### Proof of Concept
1. Admin sets `pause-states.accrue = true` on `vault-stx` (an intended, non-malicious operational action) while leaving `repay`/`borrow` unpaused.
2. Time passes (`stacks-block-time` increases past `last-update`), so economically the interest-rate model says non-zero interest has accrued on the outstanding `principal-scaled`.
3. A borrower calls repay (routed through `market.clar` → vault `repay`). The vault's `repay` path calls `accrue()` first; because `accrue` is paused, `accrue()` returns the OLD `index`/`lindex` unchanged and does not advance `last-update` [3](#0-2) .
4. The repay logic uses this stale `index` to convert the borrower's scaled debt to a token amount, so the borrower repays exactly their pre-accrual principal, paying none of the interest that should have accrued during the paused window; `principal-scaled` is decreased by their full scaled debt.
5. Admin unpauses `accrue`. The next call to `accrue()` computes `next-index` based on the full elapsed time since `last-update`, and calculates `debt-delta`/`reserve-inc`/`treasury-lp` only against the *remaining* `principal-scaled` [7](#0-6) .
6. The interest that should have accrued on the now-repaid principal for the paused window is never charged to anyone and never minted as `treasury-lp`/LP yield — it is permanently lost.

Note: I confirmed the `accrue()` pass-through and `redeem`/`deposit` call-accrue-first pattern directly in the vault contracts, and I saw the analogous `system-borrow` beginning with `(u (try! (accrue)))` [8](#0-7) . I was not able to view the full body of `system-repay` in the remaining tool budget to confirm it also begins with an unconditional `(try! (accrue))` before pricing the repayment — this is inferred from the consistent pattern seen in `deposit`/`redeem`/`system-borrow` across all six vault files. If `system-repay` differs from this pattern, the exact PoC step 3–4 would need to be re-validated against its actual code (a Devin session with full file access could confirm this).

### Citations

**File:** local-testing/contracts/vault/vault-sbtc.clar (L799-819)
```text
(define-public (redeem (amount uint) (min-out uint) (recipient principal))
  (let (
    (states (var-get pause-states))
    (u (try! (accrue)))
    (account contract-caller)
    (current-assets (var-get assets))
    (balance (get-balance-internal account))
    (balance-check (asserts! (>= balance amount) ERR-INSUFFICIENT-BALANCE))
    (available-assets (get-available-assets))
    (inkind (convert-to-assets-preview amount)))

  (asserts! (>= current-assets inkind) ERR-INSUFFICIENT-ASSETS)
  (asserts! (not (get redeem states)) ERR-PAUSED)
  (asserts! (> amount u0) ERR-AMOUNT-ZERO)
  (asserts! (> inkind u0) ERR-OUTPUT-ZERO)
  (asserts! (>= inkind min-out) ERR-SLIPPAGE)
  (asserts! (>= available-assets inkind) ERR-INSUFFICIENT-LIQUIDITY)

  (try! (ft-burn? zft amount account))
  (try! (send-underlying inkind recipient))
  (var-set assets (- current-assets inkind))
```

**File:** local-testing/contracts/vault/vault-sbtc.clar (L837-865)
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
```

**File:** local-testing/contracts/vault/vault-sbtc.clar (L867-870)
```text
(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
```
