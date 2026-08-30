### Title
`accrue()` treats the `accrue` pause as a silent pass-through instead of reverting, letting `deposit`/`borrow`/`redeem`/`system-borrow`/`system-repay` run on a stale index while interest is skipped - ([File: mainnet/contracts/vault/v0-vault-usdc.clar])

### Summary
Every vault contract (`v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, and their `local-testing` counterparts) implements `accrue` with an explicit "pass-through" branch when the `accrue` pause flag is set, instead of reverting: [1](#0-0) 

Every state-mutating vault entrypoint (`deposit`, `redeem`, `system-borrow`, `system-repay`) unconditionally calls `(try! (accrue))` first and then proceeds to mutate `assets`, `principal-scaled`, `total-borrowed`, mint/burn shares, and transfer funds - using whatever `index`/`lindex` happens to be in storage, without ever checking whether the accrual itself actually ran: [2](#0-1) [3](#0-2) 

### Finding Description
`accrue` is supposed to bring `index`/`lindex` (and thus share price / debt owed) up to date with `stacks-block-time` before any deposit, redemption, borrow, or repay is processed. This mirrors the pattern the market contract itself documents as a security control: "Always accrue before borrow/repay ... Prevents stale data exploits" (`docs/market.md`).

However, when the DAO sets `pause-states.accrue = true` on a vault, `accrue` does **not** revert the calling transaction. Instead it returns `(ok { index: idx, lindex: lidx })` - the *current, un-updated* values - and reports success:

```clarity
(if (get accrue states)
    ;; PAUSED: Pass-through without reverting
    (ok { index: idx, lindex: lidx })
    ...)
```

Because this is wrapped in `(try! (accrue))` inside `deposit`, `redeem`, `system-borrow`, and `system-repay`, `try!` sees an `ok` response and happily continues execution - it has no way to distinguish "accrual succeeded and updated state" from "accrual was skipped because paused." None of these four callers check `(get accrue states)` themselves to gate on this.

This is exactly the "pause that passes through instead of reverting" pattern: a control meant to freeze a subsystem (interest accrual) is implemented so that dependent operations proceed anyway using the stale value, rather than being blocked. `last-update` is also never advanced while `accrue` is paused (the `var-set last-update stacks-block-time` line only runs in the non-paused branch), so the vault has no on-chain signal that interest accounting silently stopped tracking real time while capital kept moving through `deposit`/`redeem`/`system-borrow`/`system-repay`.

Sequence:
1. DAO pauses `accrue` on a vault (e.g. `v0-vault-usdc`) via `pause-states`, intending to freeze interest-index changes (e.g., during an incident/migration), while leaving `deposit`/`redeem`/`borrow`/`repay` themselves unpaused (each has its own independent pause bit, e.g. `deposit`, `redeem`, `borrow`, `repay` in `pause-states`).
2. A user calls `deposit`/`redeem` (or the market calls `system-borrow`/`system-repay` on the user's behalf via `borrow`/`repay`). `(try! (accrue))` returns `ok` with the stale `index`/`lindex` instead of reverting.
3. `convert-to-shares-preview`/`convert-to-assets-preview` (in `deposit`/`redeem`) compute share/asset conversions against the frozen index/lindex while wall-clock time (and, in reality, borrower interest) has continued to accrue, and `total-borrowed`/`principal-scaled`/`assets` continue to be mutated by these calls even though the index that should reflect the passage of time never advances.
4. Because `last-update` is frozen too, once `accrue` is later unpaused, the deferred interest for the whole pause window is compounded in one lump against whatever `principal-scaled`/`assets` happen to exist *at that later moment* - not against the balances that existed throughout the paused window. Any deposits/redemptions/borrows/repays executed mid-pause therefore capture or miss interest they should not have, because the value used to convert shares↔assets during the pause was never the economically correct one for that moment in time.

### Impact Explanation
This lands on **temporary freezing of funds** (specifically of yield/interest accounting) per the in-scope impact classes: suppliers who deposit or redeem while `accrue` is paused do so against a share price that fails to reflect interest that should have been accruing, and the DAO's own accrual-freeze control is bypassed by every capital-movement entrypoint instead of blocking them, defeating the purpose of the pause and letting value move at an incorrect exchange rate for the duration of the pause window. It is not a permanent-freeze/insolvency case because interest is deferred rather than destroyed, but it directly enables mis-pricing of shares/debt for any transactions executed during the pause.

### Likelihood Explanation
Likelihood is medium: it only manifests when the DAO pauses `accrue` specifically (a legitimate operational control), while other operations (`deposit`, `redeem`, `borrow`, `repay`) remain unpaused - which is a normal, expected admin action during, e.g., an oracle incident or maintenance window, not a compromised-DAO scenario. No attacker privilege is required beyond calling the already-public `deposit`/`redeem`/`borrow`/`repay` functions during a window the DAO itself created.

### Recommendation
Make `accrue` revert (e.g., `ERR-PAUSED`) when the `accrue` pause flag is set, or have `deposit`/`redeem`/`system-borrow`/`system-repay` explicitly check `(get accrue states)` and abort rather than relying on `try!` around a call that always returns `ok`. If a genuine "pause deposits/redeems but keep the last known price" behavior is desired, gate it on the *specific* operation's own pause flag rather than silently disabling accrual underneath still-active operations.

### Proof of Concept
1. DAO calls `set-pause-states` (or equivalent) on `v0-vault-usdc` to set `accrue: true` while leaving `deposit`/`redeem`/`borrow`/`repay` bits `false`.
2. Time passes (multiple blocks), during which real borrow interest would normally have accrued via `next-index`/`next-liquidity-index`.
3. User A calls `deposit` on `v0-vault-usdc`: `(try! (accrue))` returns `(ok {index: idx, lindex: lidx})` with the pre-pause `idx`/`lidx` (verifiable via `mainnet/contracts/vault/v0-vault-usdc.clar:833-861`); `convert-to-shares-preview` mints shares at the stale (too-low) share price instead of reverting.
4. DAO unpauses `accrue`; the next call to `accrue` computes `next-index`/`next-liquidity-index` over the entire elapsed time span (including the pause window) in one step, applying it against whatever `principal-scaled`/`total-supply` exist *then* - not against the balances that existed while paused - so User A's deposit captured a share price that never reflected the true value at time of entry.

Note: I could not fully trace `next-index()`/`next-liquidity-index()` implementations (their exact interest formula) within the remaining tool budget to quantify the precise magnitude of yield mis-pricing per pause-window; this is stated as an open item for a background agent with full repo access to verify against `mainnet/contracts/vault/v0-vault-usdc.clar` in its entirety.

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L795-815)
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

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L833-861)
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

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L863-898)
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
