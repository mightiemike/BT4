## Title
Vault `deposit()` mints zero shares for a nonzero deposit due to rounding-down `convert-to-shares-preview`, with no zero-output guard (unlike `redeem()`) - (File: `mainnet/contracts/vault/v0-vault-usdc.clar` and sibling vault contracts)

### Summary
Every Zest tokenized vault (`v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`) computes shares-to-mint on deposit with a floor-division conversion that can legitimately round to `0` once `total-assets` grows larger than `total-supply` (which happens naturally as interest accrues faster than treasury shares are minted). `redeem()` explicitly guards against a zero output with `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)`, but `deposit()` has no equivalent check — only a slippage check against caller-supplied `min-out`. If `min-out` is `0` (the default a wallet/integration would pass for "no slippage protection"), a depositor's underlying asset is pulled into the vault and `assets` is incremented, while `ft-mint?` mints `0` shares to the recipient. The depositor's principal is irrecoverably absorbed into the vault with no share claim to redeem it — an on-chain analog of the PoolTogether `IdleYieldSource` H-05 finding, where lack of mantissa/precision handling zeroes out the minted amount.

### Finding Description
`convert-to-shares-preview` performs a plain floor division without any mantissa/precision scaling: [1](#0-0) 

`deposit()` uses this value (`inkind`) to mint shares, but only checks slippage — not that the minted amount is nonzero: [2](#0-1) 

Compare this to `redeem()` in the very same contract, which explicitly guards against a zero conversion result: [3](#0-2) 

The `total-assets`/`total-supply` ratio used by `convert-to-shares-preview` is not held at 1:1. Each `accrue()` call increases `assets` by the full interest delta but only mints new `zft` shares to the treasury for the smaller `fee-reserve` cut of that interest: [4](#0-3) 

This means `total-assets` structurally grows faster than `total-supply` over time, so the `ta > ts` ratio described in the referenced report (`mul-div-down amount ts ta` flooring to `0` when `ta` dominates) is a real, reachable state — not a hypothetical. Once that ratio is large enough, `(mul-div-down amount ts ta)` for a plausible deposit `amount` floors to `0`.

### Impact Explanation
When `inkind` (shares) resolves to `0`:
1. `(asserts! (>= inkind min-out) ERR-SLIPPAGE)` passes trivially if `min-out` is `0`.
2. `(try! (receive-underlying amount account))` pulls the depositor's real underlying tokens into the vault.
3. `(try! (ft-mint? zft inkind recipient))` mints `0` shares — the depositor gets no claim on the vault.
4. `(var-set assets (+ current-assets amount))` locks the deposited amount inside vault accounting with no corresponding share issued to reclaim it.

The depositor's principal is permanently and irrecoverably transferred into the vault with no share representing it — this is direct theft/permanent freezing of user funds at rest, matching the Critical impact class (funds sent, nothing returned, no way to redeem).

### Likelihood Explanation
The precondition (`total-assets` sufficiently larger than `total-supply`) is a natural, expected end-state of vault operation over time given the treasury-fee-share-minting mechanics shown in `accrue()`, not an attacker-engineered edge case. Any depositor (or an integration using a default `min-out` of `0`, which is the common "no slippage protection" pattern) making a deposit whose `amount` is small relative to the current `ta/ts` ratio will trigger this silently — no special permissions or multi-block setup required, all within a single `deposit()` call.

### Recommendation
Add an explicit zero-output guard in `deposit()` mirroring the one already present in `redeem()`, e.g. `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)`, before minting, across all vault contracts (`v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`).

### Proof of Concept
1. Let `total-assets-preview` = `ta` and `total-supply-preview` = `ts` such that `ta > 2*ts` (a state naturally reached as interest accrues faster than treasury share minting, per `accrue()`).
2. A depositor calls `deposit(amount, min-out: u0, recipient)` with `amount` such that `amount * ts < ta` (e.g., `amount = 1`).
3. `convert-to-shares-preview` computes `(mul-div-down amount ts ta)` = `0`.
4. `(asserts! (>= inkind min-out) ERR-SLIPPAGE)` → `(>= u0 u0)` → passes.
5. `receive-underlying` transfers `amount` of the depositor's underlying token into the vault.
6. `ft-mint? zft u0 recipient` mints zero shares.
7. `assets` is incremented by `amount`; the depositor holds no share and has no path to redeem the transferred `amount`. [2](#0-1) [5](#0-4) [1](#0-0)

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L306-313)
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

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L795-811)
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
