### Title
Pausing `accrue` alone permanently forfeits interest owed to lenders while `borrow`/`repay` remain active - ([File: mainnet/contracts/vault/v0-vault-sbtc.clar])

### Summary
`accrue()` implements a pass-through-instead-of-revert pattern for the `accrue` pause flag: when `accrue` is paused it simply returns the *last* cached `index`/`lindex` without recomputing interest, instead of reverting the calling transaction. Because the vault exposes `accrue`, `borrow`, `redeem`, `repay` as independently-pausable flags, an operator can pause only `accrue` while leaving `borrow`/`repay` active. Interest that should accrue during that window is silently dropped, and when `accrue` is later unpaused, `last-update` is fast-forwarded to the current time, permanently erasing any record that interest was owed for the paused interval.

### Finding Description
`accrue()` reads the `accrue` pause flag and, if set, short-circuits to `(ok { index: idx, lindex: lidx })` without touching `index`, `lindex`, or `last-update`: [1](#0-0) 

Every state-mutating entry point (`deposit`, `redeem`, `system-borrow`, `system-repay`, `flashloan`) unconditionally calls `(try! (accrue))` first and then proceeds with its own logic using whatever `index`/`lindex`/`assets` values result — it never checks whether the accrual actually happened: [2](#0-1) [3](#0-2) 

Because `borrow` and `repay` each have their own independent pause flag, they are not blocked while `accrue` is paused — `system-borrow`/`system-repay` only check `(get borrow states)` / `(get repay states)` respectively, not `(get accrue states)`: [4](#0-3) [5](#0-4) 

When `accrue` is later unpaused, `set-pause-states` explicitly forwards `last-update` to the present block time "to skip paused period," rather than replaying/crediting the missed interest: [6](#0-5) 

The net effect: `index`/`lindex` never move for the whole duration of the pause, yet outstanding debt continues to be borrowed against and repaid at the frozen (stale) index, and once unpaused the elapsed time is discarded from the interest-rate time-delta calculation permanently — the interest that should have accrued for lenders during that interval is never recovered.

### Impact Explanation
Lenders lose yield that would have otherwise accrued on outstanding debt during any window where `accrue` is paused but `borrow`/`repay` remain open — this is a permanent freezing/loss of unclaimed yield (the "High" impact class), since the missed interval is deliberately excised from `last-update` on unpause rather than caught up.

### Likelihood Explanation
This requires only the pause-flags to be set independently (a normal, single-transaction admin action via `check-dao-auth`), not a compromise of governance logic itself; the loss is a mechanical consequence of the `accrue` short-circuit combined with `borrow`/`repay` not being gated by the `accrue` flag, and is deterministic once that flag combination occurs, so likelihood is moderate — it depends on an admin/operator using the flags in that specific combination, which the contract does not prevent or warn against.

### Recommendation
Either (a) make `borrow` and `repay` also check the `accrue` pause flag so they cannot proceed while interest accrual is frozen, or (b) change `accrue()`'s paused branch to still advance the time base without skipping owed interest (e.g., accumulate a pending-interest carry that is applied on unpause) instead of silently discarding the interval when `last-update` is forwarded in `set-pause-states`.

### Proof of Concept
1. DAO/admin calls `set-pause-states` with `accrue: true`, leaving `borrow: false`, `repay: false`.
2. Borrowers continue to call `system-borrow`/`system-repay`; each call's `(try! (accrue))` hits the paused short-circuit and returns the stale `idx`/`lidx` without updating `index`, `lindex`, or `last-update`. [1](#0-0) 
3. Time passes (e.g., days) with outstanding debt and no interest recorded.
4. DAO/admin calls `set-pause-states` again with `accrue: false`; because `was-paused` is true and `now-paused` is false, `last-update` is set to `stacks-block-time` (now), discarding the entire paused interval from any future time-delta computation. [6](#0-5) 
5. Lenders permanently lose the yield that should have accrued on the outstanding debt for that interval; borrowers effectively borrowed interest-free during the pause.

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L736-739)
```text
      
      (print {
        action: "vault-set-pause-states",
        caller: tx-sender,
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L833-840)
```text
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L863-898)
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

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L900-940)
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

    (try! (receive-underlying capped-amount tx-sender))
    (var-set principal-scaled updated-scaled-principal)
    (var-set total-borrowed total-borrowed-new)
    (var-set assets (+ (var-get assets) interest-paid))

    (print {
      action: "system-repay",
      caller: contract-caller,
      data: {
        amount-requested: amount,
        amount-repaid: capped-amount,
        principal-repaid: principal-repaid,
        interest-paid: interest-paid,
        principal-scaled: updated-scaled-principal,
        total-borrowed: total-borrowed-new,
        assets: (var-get assets),
        index: idx
      }
    })

    (ok true)))
```
