### Title
Pause-Passthrough in `accrue()` Permanently Skips Protocol Fee/Yield Allocation During Paused Window - (File: `mainnet/contracts/vault/v0-vault-stx.clar` and equivalents in `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`)

### Summary
Every Zest v2 vault's `accrue` function silently passes through without updating the interest index or minting the DAO treasury's reserve-factor fee whenever the `accrue` pause flag is set, instead of reverting the calling operation. Because the treasury fee is computed only as the *marginal delta* between the old and new index at the moment a real (unpaused) accrual finally executes, any debt that is fully unwound while `accrue` stays paused causes the protocol's reserve-factor share of interest for that entire window to be permanently lost — mirroring the referenced report's core defect where fee/reward allocation depends on a gated code path that can be silently skipped rather than being unconditionally recognized at the time value moves.

### Finding Description
In `accrue()`, the function checks the `accrue` pause flag and, if set, returns the stale `{index, lindex}` pair as `ok` rather than reverting: [1](#0-0) 

The normal (unpaused) branch computes the marginal `debt-delta` between the old and new debt values from the *current* `scaled-principal`, derives `reserve-inc`/`treasury-lp` from that delta, and mints the fee share to `.dao-treasury`, only updating `last-update`, `index`, and `lindex` in that branch: [2](#0-1) 

`system-borrow` and `system-repay` (as well as `deposit`/`redeem`) all call `(try! (accrue))` first, meaning they succeed even when `accrue` is paused — they simply proceed using the stale index instead of failing: [3](#0-2) [4](#0-3) 

Because the reserve fee is a *marginal* calculation (`new-debt - old-debt` at the time the real accrual eventually runs, based on the `scaled-principal` that exists *at that moment*), any interest owed during the paused window is only ever recognized if the underlying `scaled-principal` at the time accrual resumes still reflects that debt. If a borrower fully repays (via `system-repay`, which itself calls the pass-through `accrue`) while paused, `scaled-principal` returns to zero before the next real accrual runs. When accrual later resumes, `debt-delta` is computed off a reduced (or zero) `scaled-principal`, so the reserve-factor cut owed on the interest that should have accrued during the paused window is never minted to `.dao-treasury`. This is the same root cause pattern as the report: allocation of fees/yield is tied to a specific gated code path (`withdrawTo()` in the report; `accrue()`'s unpaused branch here) instead of being recognized unconditionally when the underlying value-moving action (borrow/repay) occurs, so an administrative or accidental state (paused flag) permanently strands the protocol's fee/yield claim to that value.

### Impact Explanation
This lands on **High** impact: permanent freezing/loss of unclaimed protocol yield (the reserve-factor fee normally minted as `zft` shares to `.dao-treasury`). Depositors' principal is not at risk, but the protocol's fee revenue for interest accrued during any paused-accrual window is permanently unrecoverable once the associated debt is repaid down before the pause is lifted, since there is no retroactive/cumulative accounting of debt outstanding over time — only a point-in-time delta at the next real accrual call.

### Likelihood Explanation
Triggering this requires the `accrue` pause flag on any given vault to be set while `borrow`/`repay` remain unpaused (an operationally plausible partial-pause configuration, e.g., during an oracle or index-model incident where the team wants to halt interest accrual but still allow orderly repayment), followed by ordinary user repay activity during that window. No privileged action beyond the administrative pause toggle is needed by the exploiting party; the loss occurs from ordinary user transactions interacting with the pass-through logic.

### Recommendation
Do not allow interest-consuming operations (`system-borrow`, `system-repay`, `deposit`, `redeem`) to proceed on a stale index when `accrue` is paused — either revert them too, or decouple the reserve-fee accounting from the point-in-time marginal delta by tracking fees owed over time independent of the live `scaled-principal`, so that paused windows cannot cause protocol fee revenue to be silently and permanently dropped.

### Proof of Concept
1. Vault has outstanding debt with `scaled-principal > 0` and a positive `fee-reserve`.
2. DAO/admin sets the vault's `pause-states` so that `accrue = true` but `repay = false` (partial pause).
3. A borrower calls `system-repay` for their full debt; internally this calls `(try! (accrue))`, which hits the paused branch and returns the old `{index, lindex}` unchanged — no `treasury-lp` is minted, `last-update` is not advanced.
4. `system-repay` completes normally using the stale `idx`, reducing `scaled-principal`/`total-borrowed` to zero (or near zero) for that position.
5. Admin later unpauses `accrue`. The next real accrual computes `debt-delta` from the now-reduced `scaled-principal`, so the reserve-factor fee corresponding to the interest that accrued on the repaid debt during the paused window is never minted to `.dao-treasury` — permanently lost. [5](#0-4)

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L835-863)
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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L865-884)
```text
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
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L900-925)
```text
    (ok true)))

(define-public (system-repay (amount uint))
  (let (
        (states (var-get pause-states))
        (u (try! (accrue)))
        (scaled-principal (var-get principal-scaled))
        (idx (var-get index))
        (debt (total-debt))
        (total-borrowed-amount (var-get total-borrowed))
        (capped-amount (if (> amount debt) debt amount))
        (principal-reduction (calc-principal-ratio-reduction capped-amount scaled-principal debt))
        (capped-reduction (if (> principal-reduction scaled-principal) scaled-principal principal-reduction))
        (updated-scaled-principal (- scaled-principal capped-reduction))
        (principal-repaid (mul-div-down capped-amount total-borrowed-amount debt))
        (interest-paid (- capped-amount principal-repaid))
        (total-borrowed-new (if (> total-borrowed-amount principal-repaid) (- total-borrowed-amount principal-repaid) u0)))

    (try! (check-caller-auth))
    (asserts! (not (get repay states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)

    (try! (receive-underlying capped-amount tx-sender))
    (var-set principal-scaled updated-scaled-principal)
    (var-set total-borrowed total-borrowed-new)
    (var-set assets (+ (var-get assets) interest-paid))
```
