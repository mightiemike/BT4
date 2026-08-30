### Title
Vault `deposit()` mints 0 shares for a nonzero underlying deposit when `convert-to-shares-preview` rounds down to zero - ([File: mainnet/contracts/vault/v0-vault-usdc.clar])

### Summary
All Zest `v0-vault-*` contracts (`v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-stx.clar`) implement a share-based deposit/redeem vault. The `deposit` entry point computes shares via `convert-to-shares-preview` and mints them, but — unlike `redeem`, which explicitly requires the output amount to be nonzero — `deposit` has no check that the computed share amount (`inkind`) is greater than zero before minting and taking the user's underlying assets.

### Finding Description
`convert-to-shares-preview` rounds down using `mul-div-down`: [1](#0-0) 

Because `total-assets-preview` includes accrued lending interest on top of raw `assets`, the assets-per-share ratio (`ta/ts`) grows above 1 over time: [2](#0-1) 

`deposit` uses this rounded-down value directly as the mint amount, and only guards against zero *input* amount and slippage below `min-out` — it never asserts the *output* (`inkind`) is nonzero: [3](#0-2) 

Contrast this with `redeem` in the same contract, which explicitly guards against a zero output with `ERR-OUTPUT-ZERO`: [4](#0-3) 

Sequence to trigger:
1. Vault accrues interest over time so `total-assets-preview` (`ta`) grows larger than `total-supply-preview` (`ts`) — i.e., price-per-share > 1.
2. A depositor calls `deposit` with a small `amount` such that `mul-div-down(amount, ts, ta)` rounds down to `u0`, and with `min-out` left at `u0` (the natural default for a first-time/naive caller), so `(>= inkind min-out)` (i.e. `0 >= 0`) passes.
3. `receive-underlying` pulls the full `amount` of the user's real underlying tokens into the vault, `var-set assets` increases by the full `amount`, but `ft-mint? zft inkind recipient` mints `u0` shares to the depositor.
4. The call returns `(ok u0)` successfully — the depositor's tokens are absorbed into the vault's `assets`/share price for existing holders, and the depositor receives nothing.

This is a single-transaction defect: the guard that exists on the symmetric `redeem` path (`ERR-OUTPUT-ZERO`) is simply missing from `deposit`, so a mutation (minting) proceeds without validation of a computed zero value.

### Impact Explanation
The depositor's underlying assets are irrecoverably absorbed by the vault and redistributed to existing shareholders via the share price, with the depositor receiving zero shares and no way to reclaim the deposited funds. This is a direct theft of user funds at rest (the deposited principal), matching the Critical/High impact class for direct theft of user funds.

### Likelihood Explanation
Likelihood is highest for vaults with high-decimal underlying assets and low share-price growth thresholds, or once meaningful interest has accrued (raising `ta/ts` above 1), since any deposit amount smaller than `ta/ts` will silently round to zero shares. Naive integrators/users who pass `min-out = 0` (a common default) are exposed without any prior warning, and no special permissions or race conditions are required — a single `deposit` call is sufficient.

### Recommendation
Add an explicit check in every `deposit` function (`v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-stx.clar`) requiring `(> inkind u0)` (mirroring the existing `ERR-OUTPUT-ZERO` check already used in `redeem`) before minting shares and pulling underlying tokens, so the transaction reverts instead of silently absorbing the depositor's assets.

### Proof of Concept
1. Deploy/observe a `v0-vault-usdc` instance where interest has accrued such that `total-assets-preview() = ta > total-supply-preview() = ts` (price-per-share > 1).
2. Compute a deposit `amount` such that `mul-div-down(amount, ts, ta) == u0` (e.g., `amount` a few units when `ta/ts` ratio is large).
3. Call `(contract-call? .v0-vault-usdc deposit amount u0 tx-sender)`.
4. Observe the transaction returns `(ok u0)`: the caller's `amount` of underlying tokens was transferred into the vault (`assets` increased), but `ft-mint?` minted `u0` shares to the caller — the caller receives no shares for the tokens they provided.

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

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L339-344)
```text
(define-private (total-assets-preview)
  (let ((current-assets (var-get assets))
        (debt (debt-preview))
        (borrowed (var-get total-borrowed))
        (interest (if (> debt borrowed) (- debt borrowed) u0)))
    (+ current-assets interest)))
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
