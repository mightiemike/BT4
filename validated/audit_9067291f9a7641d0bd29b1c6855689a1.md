### Title
Zero-share deposit succeeds silently due to missing output check in `deposit()` - (File: `mainnet/contracts/vault/v0-vault-usdc.clar`, and identically in `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-stx.clar`, `v0-vault-usdh.clar`)

### Summary
The vault `deposit()` function computes minted shares via `convert-to-shares-preview`, which rounds down (`mul-div-down`), but unlike `redeem()` it never asserts the resulting share amount is non-zero. A depositor whose underlying `amount` rounds to `u0` shares under the current asset/share exchange rate still has their full underlying amount pulled into the vault, while receiving zero ztokens — the deposit silently loses value instead of reverting.

### Finding Description
`convert-to-shares-preview` rounds down when converting an underlying `amount` into shares: [1](#0-0) 

This is the same rounding-in-favor-of-the-protocol pattern described in the external report (StUSR converting underlying to shares with `mul-div-down`): for small `amount` values combined with an inflated share price (`total-assets-preview > total-supply-preview`), `mul-div-down(amount, ts, ta)` truncates to `u0`.

In `deposit()`, this `inkind` (shares) value is used directly: [2](#0-1) 

The precondition list only checks:
- `amount > u0` (`ERR-AMOUNT-ZERO`)
- `inkind >= min-out` (`ERR-SLIPPAGE`, satisfied trivially if caller passes `min-out = u0`)
- supply cap

There is **no** `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)` guard, even though the symmetric function `redeem()` explicitly includes that exact check for the equivalent asset-side computation: [3](#0-2) 

Because the check is absent on the deposit path, `receive-underlying amount account` still pulls the full underlying amount from the depositor into the vault, and `(ft-mint? zft inkind recipient)` mints zero shares to `recipient`. The transaction returns `(ok u0)` — a successful, no-error deposit that silently strands the depositor's underlying inside the vault with no corresponding claim.

This mirrors the report's core mechanism precisely: a rounding-down conversion in a single state-changing call produces a `u0` output while the "cost" side of the operation (asset pull / allowance consumption in the StUSR case) is still fully applied, and the call completes successfully instead of reverting.

### Impact Explanation
Any depositor (or any integrator building automated/looped deposit flows, e.g. `supply-collateral-add` in `market.clar`, which calls `vault-deposit` with a caller-supplied `min-shares`) who deposits an amount that rounds to zero shares under the current index permanently loses that underlying amount to the vault (distributed pro-rata to existing shareholders) with zero compensation. This is a permanent loss of user funds at rest, landing in the **Critical** impact category (permanent freezing/loss of user funds), assuming the ta/ts ratio is inflated enough (e.g., after significant interest accrual increases `total-assets` relative to `total-supply`) that small deposit amounts round to zero shares.

### Likelihood Explanation
Likelihood scales with the accrued index: the longer/higher a vault's interest accrues (raising `total-assets-preview` relative to `total-supply-preview`), the larger the "dead zone" of deposit amounts that round to `u0` shares. Because `min-out` defaults to attacker/user-controlled input and is commonly set to `u0` for convenience (as seen in integration flows), this can be triggered unintentionally by any user depositing a small amount, or deliberately by a griefer directing another contract/relayer to deposit dust amounts on a victim's behalf where the recipient is fixed but the amount is attacker-influenced.

### Recommendation
Add `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)` to `deposit()` in every vault contract (`v0-vault-usdc.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-stx.clar`, `v0-vault-usdh.clar`), mirroring the existing check already present in `redeem()`, so that deposits which would mint zero shares revert instead of silently consuming the depositor's underlying assets.

### Proof of Concept
1. Let a vault accrue interest over time so that `total-assets-preview` grows relative to `total-supply` (e.g., via `system-borrow`/`accrue` cycles), inflating the share price such that `ta / ts > 1` significantly.
2. A user calls `deposit(amount, min-out=u0, recipient)` with a small `amount` such that `mul-div-down(amount, ts, ta) == u0` (i.e., `amount < ta/ts`).
3. `convert-to-shares-preview` returns `inkind = u0`.
4. Preconditions pass: `amount > u0` ✓, `inkind (u0) >= min-out (u0)` ✓, supply cap ✓ — none of them check `inkind > u0`.
5. `receive-underlying amount account` transfers the full `amount` from the user into the vault.
6. `ft-mint? zft u0 recipient` mints zero shares.
7. Transaction returns `(ok u0)` successfully; the depositor's `amount` is now vault-owned with no corresponding ztoken balance — a silent, unrecoverable loss of the deposited amount.

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L306-317)
```text
(define-private (convert-to-shares-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ts u0)
        amount
        (if (is-eq ta u0)
            u0
            (mul-div-down amount ts ta)))))

(define-private (convert-to-assets-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L761-793)
```text
(define-public (deposit (amount uint) (min-out uint) (recipient principal))
    (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      (account contract-caller)
      (CAP-SUPPLY (var-get cap-supply))
      (current-assets (var-get assets))
      (inkind (convert-to-shares-preview amount)))

    (asserts! (not (get deposit states)) ERR-PAUSED)
    (asserts! (var-get initialized) ERR-INIT)
    (asserts! (not (var-get in-flashloan)) ERR-REENTRANCY)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (>= inkind min-out) ERR-SLIPPAGE)
    (asserts! (<= (+ current-assets amount) CAP-SUPPLY) ERR-SUPPLY-CAP-EXCEEDED)

    (try! (receive-underlying amount account))
    (try! (ft-mint? zft inkind recipient))
    (var-set assets (+ current-assets amount))

    (print {
      action: "deposit",
      caller: contract-caller,
      data: {
        depositor: account,
        recipient: recipient,
        amount: amount,
        shares-minted: inkind,
        assets: (+ current-assets amount)
      }
    })

    (ok inkind)))
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L799-815)
```text
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
