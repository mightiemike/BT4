### Title
Zero-share deposit rounding lets depositors' underlying assets be absorbed without minting compensating shares - (File: mainnet/contracts/vault/v0-vault-stx.clar)

### Summary
`deposit` computes `inkind` (shares to mint) via `convert-to-shares-preview`, which performs integer division (`mul-div-down amount ts ta`) that rounds toward zero. Unlike `redeem`, `deposit` has no `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)` guard, so a deposit whose computed share amount rounds down to `u0` still proceeds: the depositor's real underlying tokens are pulled into the vault and `assets` is increased, but zero `zft` shares are minted to the recipient. This mirrors the reported bug class (integer-division precision loss silently zeroing out a legitimate stakeholder's proportional claim) but manifests as an outright, single-transaction fund-loss path rather than a voting-power miscount.

### Finding Description
`convert-to-shares-preview` in `mainnet/contracts/vault/v0-vault-stx.clar:308-315` (and identically in `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststxbtc.clar`) is:

```clarity
(define-private (convert-to-shares-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ts u0)
        amount
        (if (is-eq ta u0)
            u0
            (mul-div-down amount ts ta)))))
``` [1](#0-0) 

Once the vault has accumulated assets/interest (`ta > ts`, i.e., price-per-share > 1, which happens naturally as interest accrues on the vault's underlying debt), any deposit amount smaller than `ta / ts` rounds `mul-div-down amount ts ta` down to `u0`.

`deposit` uses this value directly and never checks it is non-zero before mutating state:

```clarity
(define-public (deposit (amount uint) (min-out uint) (recipient principal))
    (let (
      ...
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
    ...
``` [2](#0-1) 

Compare with `redeem`, which explicitly guards against the analogous zero-output case:

```clarity
  (asserts! (> amount u0) ERR-AMOUNT-ZERO)
  (asserts! (> inkind u0) ERR-OUTPUT-ZERO)
  (asserts! (>= inkind min-out) ERR-SLIPPAGE)
``` [3](#0-2) 

The `ERR-OUTPUT-ZERO` guard exists in the codebase and is applied on the withdrawal side but is missing on the deposit side, i.e., the deposit's "mutation" (`ft-mint? zft inkind recipient` / `var-set assets ...` / pulling `receive-underlying amount account`) is evaluated without the same zero-output guard that protects `redeem`. This is a direct single-transaction analog of the report's root cause: `Scaler.scale()`/`mul-div-down` rounding a legitimate, non-zero economic contribution down to zero, and the calling function proceeding anyway instead of reverting or granting a minimum non-zero output.

Because `min_out` is caller-supplied and defaults acceptable to `u0`, the slippage check `(>= inkind min-out)` trivially passes when `inkind = u0` and `min-out = u0`, so nothing stops the zero-share deposit from executing.

### Impact Explanation
A depositor who calls `deposit` with an `amount` too small relative to the vault's `total-assets-preview`/`total-supply-preview` ratio transfers real underlying tokens (`wstx`/`usdc`/`usdh`/etc.) into the vault via `receive-underlying`, and the vault's `assets` accounting increases by that amount, but the depositor receives `u0` shares (`zft`). The deposited value is absorbed by the existing shareholders (the price-per-share for all other holders increases since `assets` grew with no corresponding share dilution), and the depositor has no shares to redeem it back. This is a permanent, direct loss of the depositor's funds in a single transaction — this lands in the **Critical** impact bucket (theft/permanent loss of user funds at rest, since the deposited principal is irrecoverably transferred to the vault and redistributed to other shareholders with no compensating claim minted to the depositor).

### Likelihood Explanation
Likelihood is moderate-to-high in principle: it requires the vault's share price (`ta`/`ts`) to exceed the deposited `amount` in the appropriate ratio, which naturally happens as interest accrues (`total-assets` grows relative to `total-supply` via the treasury-LP/interest mechanics in `accrue`), and it requires a depositor (or an attacker griefing a victim's integration/bot) to submit a small enough raw `amount` (in base units, e.g., satoshis/µSTX) with `min-out` left at `0`. Any caller with a UI/bot that doesn't hard-code a non-zero `min-out` is exposed. It is fully triggerable within one transaction with no privileged access, no oracle manipulation, and no DAO involvement, distinguishing it from out-of-scope items.

### Recommendation
Add a zero-output guard to `deposit` mirroring the one already present in `redeem`:
```clarity
(asserts! (> inkind u0) ERR-OUTPUT-ZERO)
```
placed alongside the other `asserts!` in `deposit`, in every vault contract (`v0-vault-stx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststxbtc.clar`), so a deposit that would mint zero shares reverts instead of silently forfeiting the depositor's underlying assets.

### Proof of Concept
1. Vault has accrued interest such that `total-assets-preview` (`ta`) > `total-supply-preview` (`ts`) by a factor `k` (e.g., `ta = 2 * ts`), i.e., each share is worth `k` units of underlying.
2. Depositor calls `deposit(amount, min-out=u0, recipient)` with `amount < k` (e.g., `amount = 1`, when `k = 2`).
3. `convert-to-shares-preview` computes `mul-div-down(1, ts, ta) = (1 * ts) / ta = 0` since `ta > ts`.
4. `inkind = u0`. The guard `(>= inkind min-out)` → `(>= 0 0)` → true; no other check inspects `inkind`.
5. `receive-underlying amount account` pulls the depositor's `amount` of underlying token into the vault.
6. `ft-mint? zft inkind recipient` mints `u0` shares to the depositor.
7. `var-set assets (+ current-assets amount)` — the vault's tracked assets increase by the depositor's real contribution.
8. Result: depositor's `amount` of underlying is now vault-owned, all existing shareholders' per-share value increases proportionally, and the depositor holds `0` shares — the deposit is unrecoverable in this or any subsequent transaction. This directly parallels the report's PoC pattern (stake below the rounding divisor → resulting normalized value of `0`), reproduced here through `mul-div-down` in `convert-to-shares-preview` and the absence of a `deposit`-side zero-output assertion.

### Citations

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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L763-795)
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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L810-812)
```text
  (asserts! (> amount u0) ERR-AMOUNT-ZERO)
  (asserts! (> inkind u0) ERR-OUTPUT-ZERO)
  (asserts! (>= inkind min-out) ERR-SLIPPAGE)
```
