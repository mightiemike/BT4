Confirmed: `deposit` in [1](#0-0)  lacks a zero-shares guard, while `redeem` in the same file explicitly checks `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)`. This asymmetry, combined with `convert-to-shares-preview` rounding down via `mul-div-down` [2](#0-1) , reproduces the reported bug class (division rounding to zero causing total loss of user-supplied funds) in this codebase.

### Title
Zero-share deposits accepted without an output-zero guard, allowing silent forfeiture of deposited funds - (File: `mainnet/contracts/vault/v0-vault-usdc.clar`, `v0-vault-stx.clar`, `v0-vault-usdh.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`)

### Summary
The `deposit` function in every `v0-vault-*.clar` contract computes `inkind` (shares to mint) via `convert-to-shares-preview`, which uses floor division (`mul-div-down`). If the exchange rate (`total-assets-preview / total-supply-preview`) is high enough relative to the deposited `amount`, `inkind` rounds down to `u0`. Unlike `redeem`, which explicitly rejects a zero output with `ERR-OUTPUT-ZERO`, `deposit` has no equivalent check — it only enforces `(>= inkind min-out)`, which is satisfied trivially when `min-out` is `u0` (the default a user might pass, or simply neglect to think about). The underlying tokens are still pulled from the depositor and added to vault `assets`, but zero shares are minted to the `recipient`, so the depositor's funds are irrecoverably transferred into the vault for the benefit of existing shareholders.

### Finding Description
`convert-to-shares-preview` in [2](#0-1)  returns `(mul-div-down amount ts ta)` whenever both `total-supply-preview` (`ts`) and `total-assets-preview` (`ta`) are non-zero. This is integer floor division; if `amount * ts < ta`, the result is `u0`.

`deposit`, at [1](#0-0) , binds `inkind` from this preview at line 768, then only asserts:
- `amount > u0` (line 773)
- `inkind >= min-out` (line 774)
- supply cap not exceeded (line 775)

There is no `(asserts! (> inkind u0) ...)`. If `min-out` is `u0` and `inkind` computes to `u0`, all three assertions pass. The function then executes `(try! (receive-underlying amount account))` (line 777), pulling the depositor's tokens into the vault, and `(try! (ft-mint? zft inkind recipient))` (line 778) mints zero shares. `(var-set assets (+ current-assets amount))` (line 779) permanently folds the deposited amount into vault assets, benefiting all existing shareholders pro-rata while the depositor receives nothing.

This mirrors the reported bug class precisely: a division (`volume / price` there, `amount * ts / ta` here) that rounds to zero under low-value/high-exchange-rate conditions, paired with a missing guard that should have rejected the zero-output transaction before value was transferred (the "mutation evaluated before its guard" pattern — `receive-underlying`/`ft-mint?`/`var-set assets` execute unconditionally once the (insufficient) asserts pass, since `min-out=u0` provides no protection by default). The same pattern (`convert-to-shares-preview`, `deposit`, redeem's contrasting `ERR-OUTPUT-ZERO` check) recurs identically across `v0-vault-stx.clar`, `v0-vault-usdh.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, and `v0-vault-ststxbtc.clar` per the earlier grep of `deposit`/`convert-to-shares-preview` definitions in `mainnet/contracts/vault/*.clar`.

### Impact Explanation
This is High severity: temporary/permanent freezing (in practice, total loss) of the depositor's principal for a single deposit transaction. The depositor's tokens become indistinguishable vault assets that only benefit other shareholders; the depositor gets zero `zft` shares and no path to reclaim the lost amount. This falls under "permanent freezing of funds" (belonging to the depositor) since there's no shares representing their claim.

### Likelihood Explanation
Medium. It occurs whenever the vault's share price (`ta/ts`) has grown large relative to a user's deposit amount — e.g., after substantial interest accrual with a small total supply, or if a user (or a naive integrating frontend/contract) submits a very small deposit amount and defaults `min-out` to `u0`. Because `min-out` is a caller-supplied parameter with no enforced minimum, and many integrations pass `0` when they don't intend to use slippage protection, this can be triggered unintentionally, not just by a sophisticated attacker.

### Recommendation
Add an explicit zero-output guard to `deposit`, symmetric to the one already present in `redeem`:
```clarity
(asserts! (> inkind u0) ERR-OUTPUT-ZERO)
```
placed before `receive-underlying` is invoked, in each of `v0-vault-usdc.clar`, `v0-vault-stx.clar`, `v0-vault-usdh.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, and `v0-vault-ststxbtc.clar`.

### Proof of Concept
1. Vault has been running long enough (or `assets`/`total-borrowed` state has grown) such that `total-assets-preview() / total-supply-preview()` (the share price) is large, e.g. `ta = 10_000_000_000`, `ts = 1`.
2. A depositor calls `deposit(amount=5, min-out=0, recipient=depositor)` on e.g. `v0-vault-usdc.clar`.
3. `convert-to-shares-preview(5)` computes `mul-div-down(5, 1, 10_000_000_000) = 0` [2](#0-1) .
4. All guards pass: `amount(5) > 0`, `inkind(0) >= min-out(0)`, cap not exceeded.
5. `receive-underlying` transfers 5 USDC from the depositor into the vault; `ft-mint?` mints `0` `zft` to the depositor; `assets` increases by 5 [3](#0-2) .
6. The depositor has permanently lost their 5 USDC with zero shares to redeem it; existing shareholders' redemption value increases proportionally.

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
