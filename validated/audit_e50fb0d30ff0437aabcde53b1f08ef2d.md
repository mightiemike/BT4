### Title
Deposit function mints zero shares for small deposits without any output check, silently absorbing the depositor's underlying assets into vault state - (File: `mainnet/contracts/vault/v0-vault-stx.clar` and equivalent `deposit` in all six vault contracts)

### Summary
The `deposit` function in every Zest vault (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`) computes the shares to mint (`inkind`) via `convert-to-shares-preview`, which uses floor division (`mul-div-down`). When the deposited `amount` is small relative to the current share price (`total-assets / total-supply`), `inkind` truncates to `0`. Unlike `redeem`, which explicitly guards against a zero output with `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)`, `deposit` has no equivalent check. As a result the underlying tokens are pulled from the user and the vault's `assets` accounting is incremented, but `0` shares are minted to the depositor, permanently stranding the deposited value for that user.

### Finding Description
`convert-to-shares-preview` performs: [1](#0-0) 

using integer floor division `mul-div-down`: [2](#0-1) 

`deposit` uses this preview to compute `inkind` and only validates a slippage floor and a supply cap, never that the computed share amount is non-zero: [3](#0-2) 

Contrast this with `redeem`, which explicitly guards against a zero conversion result with `ERR-OUTPUT-ZERO`: [4](#0-3) 

Once the vault has accrued interest for any period of time, `total-assets` (`ta`) grows relative to `total-supply` (`ts`), so the share price `ta/ts` exceeds `1`. Any deposit `amount` such that `amount * ts < ta` truncates to `inkind = 0` under `mul-div-down`. Since `min-out` defaults to `u0` for a normal user call, the slippage check `(asserts! (>= inkind min-out) ERR-SLIPPAGE)` passes trivially even when `inkind` is `0`. `deposit` then:
1. Transfers the real underlying `amount` from the user into the vault (`receive-underlying`).
2. Mints `0` shares to the recipient (`ft-mint? zft 0 recipient` is a no-op).
3. Increments `assets` by the full `amount`, socializing the value across all existing shareholders.

This is the same rounding-truncation root cause described in the VotingEscrow report — a per-deposit derived value ("bias"/"shares") divided by a denominator that can exceed the numerator for small deposits, silently truncating to zero without any minimum-deposit guard — except in the Zest vault this actually strips the depositor of the value they deposited (permanently, since it accrues to other shareholders) rather than merely delaying a voting-power update.

### Impact Explanation
The depositor's tokens are transferred into the vault and increase the vault's tracked `assets`, but the depositor receives `0` zft shares and therefore has no claim on that value — it is permanently redistributed to existing shareholders. This is a permanent loss of the depositor's funds (they cannot ever redeem the value back), landing in the "permanent freezing of funds" impact class relative to the affected depositor.

### Likelihood Explanation
Likelihood scales with the vault's share price (`total-assets/total-supply`, which only grows via interest accrual) and depositors sending very small `amount`s (e.g., dust deposits, or amounts near `assets`/`supply` ratio boundaries). This becomes progressively easier to trigger unintentionally as the vault accrues more interest over its lifetime, and any UI/integration that computes deposit amounts programmatically without a minimum threshold could hit this path. No `min-out` protection helps because default `min-out = 0` passes trivially.

### Recommendation
Add an explicit non-zero output check in `deposit`, mirroring `redeem`'s `ERR-OUTPUT-ZERO` guard:
```clarity
(asserts! (> inkind u0) ERR-OUTPUT-ZERO)
```
placed before `receive-underlying`/`ft-mint?`/`var-set assets`, across all six vault contracts (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`).

### Proof of Concept
1. Let the vault run for a period so that `total-assets-preview()` (`ta`) grows larger than `total-supply-preview()` (`ts`) due to interest accrual, e.g. `ta = 2_000_000`, `ts = 1_000_000` (share price = 2.0).
2. A depositor calls `deposit(amount=1, min-out=0, recipient)`.
3. `convert-to-shares-preview(1)` computes `mul-div-down(1, 1_000_000, 2_000_000) = (1 * 1_000_000) / 2_000_000 = 0` (floor division).
4. `(asserts! (>= inkind min-out) ERR-SLIPPAGE)` passes since `0 >= 0`.
5. `receive-underlying` pulls `1` unit of underlying from the depositor.
6. `ft-mint? zft 0 recipient` mints zero shares.
7. `var-set assets (+ current-assets 1)` — the vault's asset total increases by the depositor's contribution, but the depositor holds no shares representing it; the value is now split among all other zft holders. [3](#0-2) [1](#0-0)

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L147-148)
```text
(define-private (mul-div-down (x uint) (y uint) (z uint))
  (/ (* x y) z))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L308-315)
```text
(define-private (convert-to-shares-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ts u0)
        amount
        (if (is-eq ta u0)
            u0
            (mul-div-down amount ts ta)))))
```

**File:** local-testing/contracts/vault/vault-stx.clar (L765-797)
```text
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

(define-public (redeem (amount uint) (min-out uint) (recipient principal))
```

**File:** local-testing/contracts/vault/vault-stx.clar (L799-817)
```text
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
