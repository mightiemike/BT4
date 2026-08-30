### Title
Missing zero-shares guard in `deposit()` strands depositor's underlying tokens when vault assets are wiped to zero - (File: `mainnet/contracts/vault/v0-vault-stx.clar` and equivalent `v0-vault-*.clar` vaults)

### Summary
Every vault contract (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) computes minted shares via `convert-to-shares-preview`, which returns `u0` whenever total-supply is non-zero but total-assets is zero. Unlike `redeem()`, which explicitly guards against a zero output with `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)`, `deposit()` has no equivalent check. A depositor calling `deposit` with the default `min-out` of `u0` while the vault is in this state will have their underlying tokens transferred into the vault and `assets` incremented, but will receive zero vault shares in return — their principal becomes permanently unclaimable.

### Finding Description
The share-conversion helper is defined identically in every vault: [1](#0-0) 

When `ts` (total-supply-preview) is non-zero but `ta` (total-assets-preview) is zero, the function returns `u0` shares for *any* non-zero deposit amount.

`deposit()` uses this value (`inkind`) only against a slippage bound supplied by the caller, with no absolute floor: [2](#0-1) 

If the caller passes `min-out = u0` (the natural default for a "no slippage protection" deposit, or simply an unaware integration), `(asserts! (>= inkind min-out) ERR-SLIPPAGE)` passes trivially with `inkind = u0`. The function then:
1. Transfers `amount` of the underlying token from the depositor into the vault (`receive-underlying`).
2. Mints `u0` zft shares to the recipient (`ft-mint? zft inkind recipient` mints nothing).
3. Increases `assets` by the full deposited `amount`.

The depositor's funds become part of `assets`, increasing the redeemable value for every *other* existing shareholder, while the depositor holds no shares and therefore has no path to reclaim any of the value they contributed.

By contrast, `redeem()` in the very same contract explicitly reverts on this same zero-output condition: [3](#0-2) 

This asymmetry — a guard present on the withdrawal path but missing on the deposit path — is the root cause. It matches the report's bug class: a state-affecting operation (`totalEthPerEsembr`/shares) is silently skipped for a degenerate divisor case, while the value transfer that was supposed to be compensated by that state update still executes, permanently stranding the sent value.

The degenerate state (`ts > 0`, `ta == 0`) is reachable in this protocol whenever total vault assets are driven to zero while shares are still outstanding — e.g. via `socialize-debt`, which is designed to write down vault assets/liquidity index to reflect a loss: [4](#0-3) 

Once `assets` and accrued excess interest have both been fully written down to zero (a legitimate, protocol-triggered event, not requiring any DAO compromise), any subsequent `deposit()` call with `min-out = u0` will succeed, minting zero shares while consuming the depositor's underlying.

### Impact Explanation
This is a single-transaction, single-function-call defect: no interference from a second user is required beyond the pre-existing wiped-out vault state — the affected user's own `deposit` call is where their principal is stranded. The impact falls under "permanent freezing of funds": the depositor's underlying tokens are absorbed into the vault's `assets` (benefiting other shareholders) while the depositor is left holding zero shares and has no on-chain mechanism to redeem or reclaim the value they sent.

### Likelihood Explanation
The precondition (`total-supply-preview != 0` while `total-assets-preview == 0`) requires the vault to reach a fully-wiped-out state via `socialize-debt` (a legitimate, reachable protocol operation for handling bad debt) while shares remain outstanding. Once in that state, any integrator or user calling `deposit` with a default/zero `min-out` — which is the common calling pattern for a "no slippage" deposit — will trigger the loss without any special crafting, making exploitation/occurrence straightforward once the precondition is met.

### Recommendation
Add the same zero-output guard to `deposit()` that already exists in `redeem()`:
```clarity
(asserts! (> inkind u0) ERR-OUTPUT-ZERO)
```
placed alongside the other pre-condition asserts in `deposit()`, for every vault contract (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`).

### Proof of Concept
1. Vault `v0-vault-stx` has non-zero `total-supply` (shares outstanding from earlier depositors).
2. `socialize-debt` is invoked (a legitimate write-down path) such that `assets` and any excess interest are reduced to `u0`, making `total-assets-preview() == u0` while `total-supply-preview() > u0`.
3. A user calls `deposit(amount, min-out: u0, recipient: user)`.
4. `convert-to-shares-preview(amount)` evaluates `ts != 0` → checks `ta == 0` → returns `u0`.
5. `(asserts! (>= inkind min-out) ERR-SLIPPAGE)` passes since `u0 >= u0`.
6. `receive-underlying` pulls `amount` from the user into the vault; `ft-mint? zft u0 recipient` mints no shares; `assets` is incremented by `amount`.
7. The user's `amount` is now part of vault `assets`, benefiting existing shareholders' redemption value, while the depositor holds `u0` shares and cannot redeem anything — their funds are permanently stranded. [2](#0-1) [1](#0-0)

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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L797-812)
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
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L942-960)
```text
(define-public (socialize-debt (scaled-amount uint))
  (let ((scaled-principal (var-get principal-scaled))
        (borrowed (var-get total-borrowed))
        (idx (var-get index))
        (current-assets (var-get assets))
        (current-lindex (var-get lindex))
        (old-total-assets (total-assets))
        (debt-reduction (mul-div-down scaled-amount idx INDEX-PRECISION))
        (principal-reduction (if (> scaled-principal u0)
                                (mul-div-down scaled-amount borrowed scaled-principal)
                                u0))
        ;; Write down lindex proportionally to loss in total-assets
        (new-lindex (if (and (> old-total-assets u0) (> old-total-assets debt-reduction))
                       (mul-div-down current-lindex (- old-total-assets debt-reduction) old-total-assets)
                       u0)))

    (try! (check-caller-auth))
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

```
