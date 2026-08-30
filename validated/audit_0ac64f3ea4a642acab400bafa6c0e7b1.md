This confirms the analog. In `deposit`, the `inkind` (shares to mint) is computed via `convert-to-shares-preview` at line 768, but unlike `redeem` — which explicitly guards with `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)` at line 809 — `deposit` has no equivalent zero-output check. The only checks are `(> amount u0)` (line 773, guards the *input*, not the computed shares) and `(>= inkind min-out)` (line 774, which is satisfied trivially when `min-out` is `0` and `inkind` is `0`). Since `convert-to-shares-preview` (lines 306-313) returns `mul-div-down amount ts ta`, a small `amount` relative to `ta`/`ts` rounds down to `u0`, exactly mirroring the reported GM-token bug: the user's underlying tokens are pulled in via `receive-underlying` (line 777) and vault assets incremented, while `ft-mint?` mints `u0` shares to the recipient — the deposit succeeds, funds move, but the depositor receives nothing in return. [1](#0-0) [2](#0-1) [3](#0-2) 

This same pattern is duplicated identically across all six vaults (`v0-vault-usdc.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-stx.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`), so the missing check is systemic.

### Title
Missing zero-shares guard in `deposit` allows silent loss of deposited underlying tokens - ([File: mainnet/contracts/vault/v0-vault-usdc.clar] and all other vault contracts)

### Summary
The `deposit` function in all `v0-vault-*.clar` contracts computes the number of shares to mint (`inkind`) via `convert-to-shares-preview`, a rounding-down division, but never asserts that the result is non-zero before minting and transferring in the underlying asset. This mirrors the reported "GM tokens deposited but no shares minted" bug class: a sufficiently small deposit relative to the vault's `total-assets`/`total-supply` ratio rounds `inkind` to `0`, the deposit still succeeds, the underlying tokens are pulled from the user and added to vault `assets`, but `ft-mint?` mints `0` shares — the user's tokens are irrecoverably absorbed by the vault for nothing.

### Finding Description
`convert-to-shares-preview` performs `(mul-div-down amount ts ta)` when both `total-supply-preview` (`ts`) and `total-assets-preview` (`ta`) are non-zero [1](#0-0) . This is integer division that truncates toward zero, so whenever `amount * ts < ta`, the result is `0`.

In `deposit`, the guard set is:
- `(> amount u0)` — checks the *input* deposit amount is non-zero, not the shares that will be minted.
- `(>= inkind min-out)` — a slippage check that a caller can trivially satisfy by passing `min-out = u0`, which will pass even when `inkind` is `0`.

There is no `(> inkind u0)` assertion, unlike the exact same code pattern in `redeem`, which does include `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)` [4](#0-3) . Consequently `deposit` proceeds to call `receive-underlying` (pulling the user's tokens into the vault) and `ft-mint? zft inkind recipient` (minting `0` shares) and increments `assets` by the full deposited `amount` [5](#0-4) . The transaction returns `(ok inkind)` = `(ok u0)` successfully; nothing reverts.

This is a genuine analog to the external report's root cause (a value — shares/USD conversion — that truncates to zero for small-enough inputs, consumed without a zero-check, causing deposited value to be stranded), applied within a single transaction of the vault's own `deposit` entry point, not requiring any cross-user interaction, oracle manipulation, or DAO compromise.

### Impact Explanation
A user whose deposit amount is small relative to the current `total-assets`/`total-supply` ratio (which grows over time as the vault accrues interest, or can be pushed via a large first depositor establishing a high share price) will have their underlying tokens permanently absorbed into the vault's `assets` variable while receiving zero shares in return, i.e., zero claim on those assets. This is a permanent freezing/loss of the user's deposited funds — no shares exist to redeem the value back — landing on the "permanent freezing of funds" impact class.

### Likelihood Explanation
This does not require a malicious actor: any ordinary user whose deposit is small enough to trigger truncation (e.g., a vault with high `ta/ts` ratio after significant interest accrual, and a user depositing a very small `amount`) will trigger it, exactly the scenario the external report describes for GM tokens. It can also be induced deliberately: a user (or attacker griefing another via a "donation" that skews the ratio, though that is out of scope per the two-user rule) increases `ta` per share so that subsequent small deposits round to zero shares even without malice, simply due to normal vault operation over time as `total-assets-preview` grows relative to `total-supply-preview`.

### Recommendation
Add an explicit zero-shares guard to `deposit`, mirroring the existing `redeem` check:
```clarity
(asserts! (> inkind u0) ERR-OUTPUT-ZERO)
```
placed immediately after computing `inkind` and before `receive-underlying`/`ft-mint?` are invoked, in all six vault contracts (`v0-vault-usdc.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-stx.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`).

### Proof of Concept
1. Let the vault accumulate interest so that `total-assets-preview` (`ta`) grows well beyond `total-supply-preview` (`ts`) — e.g., `ta = 10_000_000_000` (10,000 USDC, 6 decimals) and `ts = 1_000_000` (1 share unit), giving a share price of `10,000` underlying units per share.
2. A user calls `deposit(amount = 5000, min-out = 0, recipient = user)` (a small deposit of 0.005 USDC).
3. Inside `deposit`, `inkind = convert-to-shares-preview(5000) = mul-div-down(5000, 1_000_000, 10_000_000_000) = 0` (integer truncation) [1](#0-0) .
4. The asserts at lines 773-775 all pass: `amount > 0` ✓, `inkind (0) >= min-out (0)` ✓, supply cap not exceeded ✓.
5. `receive-underlying(5000, account)` pulls 5000 units of USDC from the user into the vault; `ft-mint? zft 0 recipient` mints zero shares; `assets` is incremented by 5000 [5](#0-4) .
6. The call returns `(ok u0)` — success, but the user now holds zero vault shares and has permanently lost the 5000 units of underlying they deposited, with no way to redeem them back.

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
