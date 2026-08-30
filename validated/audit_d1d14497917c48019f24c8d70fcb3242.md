### Title
`accrue()` silently no-ops when paused instead of reverting, letting `repay`/`redeem` lock in a stale interest index — (File: `mainnet/contracts/vault/v0-vault-stx.clar` and sibling vaults)

### Summary
Every vault's `accrue` function is gated by a `pause-states` flag. When the `accrue` sub-flag is paused, the function does not revert — it silently returns the *old* `index`/`lindex` pair as if nothing were wrong: [1](#0-0) 

Callers such as `redeem`, `system-repay`, `system-borrow`, and `deposit` call `(try! (accrue))` unconditionally and then use `(var-get index)`/`(var-get lindex)` to price shares/debt, without ever checking whether `accrue` itself is paused: [2](#0-1) [3](#0-2) 

### Finding Description
`accrue` is the single source of truth for the vault's interest index. Its guard (`(get accrue states)`) is supposed to prevent index recalculation during a maintenance window, but instead of reverting the whole transaction (as `deposit`'s own `ERR-PAUSED` check does for its own flag), it returns `(ok {index: idx, lindex: lidx})` — i.e., success, with unchanged values — while `last-update` is left un-advanced only in the non-pass-through branch: [1](#0-0) 

Because this is a "pass-through" rather than a revert, any function that depends on `accrue` for correct pricing (`redeem`, `system-repay`, `system-borrow`) still executes to completion using the frozen index, even though the protocol's real-time debt/liquidity value (based on `stacks-block-time`, which keeps advancing) has diverged from the cached `index`/`lindex`. When `pause-states.accrue` is later cleared, the next `accrue` call computes `next-index`/`next-liquidity-index` for the *entire* elapsed window in one jump, socializing all interest that accrued during the pause onto whichever depositors/borrowers are still holding positions at that time.

A user who calls `system-repay` or `redeem` while `accrue` is paused settles their debt/withdraws their shares at the stale (lower-interest) index, permanently avoiding the interest that would otherwise have been charged/credited for that window. That unaccrued interest does not vanish — the formula in the non-paused branch (`debt-delta`, `reserve-inc`, `treasury-lp`) is applied to the *whole* scaled-principal pool once accrual resumes, so the cost/benefit that the exiting user escaped is shifted onto the remaining pool participants.

### Impact Explanation
This is a single-transaction, single-block state-consistency bug: the guard (`accrue` pause) is evaluated, reports success, but does not stop the mutation (`repay`/`redeem`) that depends on the very value the guard was supposed to protect. This falls squarely in the "pause that passes through instead of reverting" analog class. The result is theft of unclaimed yield: a borrower can permanently evade interest owed to lenders by acting inside the pause window, and that shortfall is absorbed by remaining vault participants when accrual resumes. This matches the in-scope **High** impact bucket: "theft of unclaimed yield or royalties."

### Likelihood Explanation
Exploitation requires only that the `accrue` pause sub-flag be active (an operational/incident-response state that the DAO can and does toggle) while the `repay`/`system-borrow`/`redeem` sub-flags remain unpaused — a state combination the pause-state bitmap explicitly allows since each operation has its own independent flag. No collusion between users, DAO compromise, or oracle manipulation is needed; a single actor watching for a maintenance pause and repaying/redeeming inside that window is sufficient.

### Recommendation
`accrue` should not return `ok` with stale values when paused; functions that price shares/debt off `index`/`lindex` (`deposit`, `redeem`, `system-borrow`, `system-repay`) should explicitly check the `accrue` pause flag themselves and revert (e.g., `ERR-PAUSED`) rather than silently proceeding with an out-of-date index. Alternatively, `accrue`'s pass-through branch should return an error instead of `(ok ...)`, forcing every dependent operation to halt until accrual is unpaused.

### Proof of Concept
1. DAO/operator sets `pause-states.accrue = true` on `v0-vault-stx` (e.g., during a monitored incident), leaving `repay`/`system-repay` and `redeem` unpaused.
2. Time passes (`stacks-block-time` advances), during which real interest should accrue on outstanding debt, per `next-index`/`next-liquidity-index`.
3. A borrower calls `system-repay`, whose first `let` binding calls `(try! (accrue))`; because `accrue` is paused it returns `(ok {index: idx, lindex: lidx})` unchanged rather than reverting: [4](#0-3) 
4. The borrower's debt is settled using the stale `idx`, avoiding the interest that has actually elapsed.
5. Operator later sets `pause-states.accrue = false`. The next call to `accrue` computes `next-index` for the entire elapsed period at once and applies `debt-delta`/`reserve-inc` to the current `scaled-principal` pool — which no longer includes the borrower who already exited — shifting that unaccrued interest cost onto remaining participants.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L797-820)
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
  
  (print {
    action: "redeem",
```

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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L902-920)
```text
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
```
