### Title
Interest-Accrual Pause Silently No-Ops Instead of Reverting, Letting Borrow/Repay/Deposit/Redeem Proceed on a Frozen Index - (File: `mainnet/contracts/vault/v0-vault-sbtc.clar`)

### Summary
Every lending vault's `accrue()` function treats its `accrue` pause flag as a silent pass-through: when paused, it returns the last-known `{index, lindex}` pair instead of reverting, while `system-borrow`, `system-repay`, `deposit`, and `redeem` each gate only on their *own* independent pause flag (`borrow`, `repay`, `deposit`, `redeem`) rather than on `accrue`. This lets normal lending operations continue to execute against a frozen interest index while accrual (and the protocol's reserve-fee minting to the DAO treasury) is halted.

### Finding Description
Each vault stores a single `pause-states` tuple with independent boolean flags: [1](#0-0) 

`accrue()` checks only its own `accrue` flag. If set, it returns the currently stored `index`/`lindex` unchanged and skips updating `last-update` and skips minting the reserve fee to `.dao-treasury` — it never reverts: [2](#0-1) 

`system-borrow` and `system-repay` call `(try! (accrue))` first, then separately assert on their own `borrow`/`repay` flags — not on the `accrue` flag: [3](#0-2) [4](#0-3) 

`deposit`/`redeem` follow the same shape, calling `accrue` and then gating on their own flag only: [5](#0-4) 

Because `accrue`'s pause branch returns `(ok {...})` with the *stale* values rather than propagating `ERR-PAUSED`, none of the calling functions detect that accrual was skipped — they proceed as if the index/lindex were current. This identical pattern is duplicated verbatim across all six vaults (`v0-vault-sbtc`, `v0-vault-ststx`, `v0-vault-ststxbtc`, `v0-vault-stx`, `v0-vault-usdc`, `v0-vault-usdh`), confirming it is a systemic, not one-off, control-flow gap.

### Impact Explanation
While the `accrue` flag is paused and `borrow`/`repay` remain unpaused:
- New debt taken via `system-borrow` is scaled against a frozen `index`, so it accrues zero interest for the entire duration of the pause — an effectively interest-free loan at suppliers' expense.
- The protocol reserve-fee mint to `.dao-treasury` (`treasury-lp`) only occurs in the non-paused branch of `accrue`, so the DAO treasury permanently loses the yield it would have collected for that window.
- Depositors/redeemers continue to price shares off the same stale `lindex`, meaning yield that should have accrued to suppliers from capital borrowed during the pause is never realized once accrual resumes (the paused period is simply skipped, not backfilled).

This lands on the in-scope impact class of theft/permanent freezing of unclaimed yield (protocol reserve + supplier interest) for the duration of any `accrue`-only pause, which can be indefinite if unpause is delayed.

### Likelihood Explanation
No special privilege is required by an attacker beyond ordinary use of `borrow`/`repay`/`deposit`/`redeem` while an operator has paused only the `accrue` flag (a plausible, isolated operational action, e.g., in response to an interest-rate-model concern). The bug is purely a same-transaction control-flow gap — the pause branch of `accrue` returns success instead of an error — so it triggers automatically the first time any lending operation is called while `accrue` alone is paused; no race or multi-party interference is needed.

### Recommendation
Have the paused branch of `accrue()` return an error (e.g., `ERR-PAUSED`) instead of `(ok {index, lindex})`, or explicitly propagate the `accrue` pause flag as a precondition inside `system-borrow`, `system-repay`, `deposit`, and `redeem` so that all of these operations halt (rather than silently operate on a stale index) whenever accrual itself is paused.

### Proof of Concept
1. DAO/authorized pauser sets `pause-states.accrue = true` on `v0-vault-sbtc` (e.g., to halt interest calculations) while leaving `borrow`/`repay`/`deposit`/`redeem` flags `false`.
2. A user (via `v0-4-market.clar`'s `vault-system-borrow` routing) calls `system-borrow`; inside it, `(try! (accrue))` hits the paused branch and returns the old `{index, lindex}` with `(ok ...)` — no revert, no state mutation, no treasury mint.
3. `system-borrow`'s own asserts only check `(get borrow states)`, which is `false`, so the borrow proceeds normally, scaling `principal-scaled` against the frozen `index`.
4. Time/blocks pass while `accrue` stays paused; every subsequent borrow/repay/deposit/redeem keeps using the same frozen index, so debt taken during the window accrues no interest and `.dao-treasury` never receives the reserve-fee mint for that period.
5. Once `accrue` is unpaused, accrual resumes from the frozen index — the yield that should have accumulated during the pause window is permanently lost rather than caught up. [6](#0-5)

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L98-115)
```text
;; -- Pause states
(define-data-var pause-states
  {
    deposit: bool,
    redeem: bool,
    borrow: bool,
    repay: bool,
    accrue: bool,
    flashloan: bool
  }
  {
    deposit: false,
    redeem: false,
    borrow: false,
    repay: false,
    accrue: false,
    flashloan: false
  })
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L795-898)
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
    caller: contract-caller,
    data: {
      redeemer: account,
      recipient: recipient,
      shares-burned: amount,
      amount-received: inkind,
      assets: (- current-assets inkind)
    }
  })

  (ok inkind)))

;; -- Lending operations -----------------------------------------------------

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
