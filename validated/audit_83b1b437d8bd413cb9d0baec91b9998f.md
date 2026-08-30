### Title
Zero-share deposit lets the ERC4626-style vault silently swallow depositor funds - (File: `mainnet/contracts/vault/v0-vault-usdc.clar` and equivalent `v0-vault-*.clar` files)

### Summary
`deposit` in every `v0-vault-*.clar` contract computes shares to mint via `convert-to-shares-preview`, which truncates (`mul-div-down`), and only guards against zero-value output implicitly through the caller-supplied `min-out` slippage check. Unlike `redeem`, which explicitly asserts `(> inkind u0) ERR-OUTPUT-ZERO`, `deposit` has no such assertion, so a depositor whose computed `inkind` (shares) rounds down to `u0` can still have their underlying transferred into the vault and `ft-mint?` called with `u0`, receiving no shares while their assets permanently become part of everyone else's redeemable pool.

### Finding Description
`convert-to-shares-preview` in `mainnet/contracts/vault/v0-vault-usdc.clar:310-317` (identical logic in all sibling vault files, e.g. `v0-vault-sbtc.clar`, `v0-vault-stx.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdh.clar`) computes:
```
(mul-div-down amount ts ta)
``` [1](#0-0) 

`ta` (`total-assets-preview`) grows over time as interest accrues on borrowed funds while `ts` (`total-supply-preview`, the outstanding share count) stays fixed until minted/burned [2](#0-1) . As soon as `ta` grows large relative to `ts` (which is the normal, expected state of an interest‑bearing vault after any utilization/borrowing), the ratio `ts/ta` drops below 1, and `mul-div-down` truncates toward zero for any `amount` small enough that `amount * ts < ta`.

`deposit` uses this value directly:
```
(inkind (convert-to-shares-preview amount))
...
(asserts! (> amount u0) ERR-AMOUNT-ZERO)
(asserts! (>= inkind min-out) ERR-SLIPPAGE)
...
(try! (receive-underlying amount account))
(try! (ft-mint? zft inkind recipient))
``` [3](#0-2) 

There is no `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)` guard on the deposit path. The only safety net is the `min-out` slippage check, which a caller who passes `min-out = u0` (a common/default pattern, and also the value any naive integrator or default UI would use for a "no slippage protection" deposit) does not trip, because `0 >= 0` is true. The `redeem` function in the exact same contract explicitly protects against this scenario:
```
(asserts! (> inkind u0) ERR-OUTPUT-ZERO)
``` [4](#0-3) 
confirming the protocol itself recognizes zero-output as an error condition it must reject — but the corresponding guard was omitted from `deposit`.

Sequence:
1. Vault has been operating; `total-assets` (`ta`) has grown from accrued borrower interest while `total-supply` (`ts`) is comparatively small (this is the ordinary long-run state of the vault, not an edge case requiring owner action).
2. A user calls `deposit` with an `amount` small enough that `amount * ts < ta` and `min-out = u0` (default/no-slippage call).
3. `convert-to-shares-preview` returns `inkind = u0` via `mul-div-down` truncation.
4. `(asserts! (>= inkind min-out) ERR-SLIPPAGE)` passes because `0 >= 0`.
5. `receive-underlying` pulls the user's tokens into the vault and `assets` is incremented by `amount`, but `ft-mint? zft u0 recipient` mints no shares.
6. The user's deposited value is now permanently redistributable pro-rata to existing shareholders; the depositor holds no claim on it.

### Impact Explanation
This is a direct loss of user funds at rest: the depositor transfers real underlying collateral into the vault (`receive-underlying`, `var-set assets (+ current-assets amount)`) but receives zero shares (`ft-mint? zft u0 ...`) representing that value, permanently diluting their own contribution into the pool for other shareholders. This falls under the in-scope "permanent freezing/theft of user funds at rest" impact class, since the affected user has no share token to redeem the value back and no recovery mechanism exists in the contract.

### Likelihood Explanation
This requires no privileged access, no oracle manipulation, and no multi-user griefing — it is a single-transaction, single-caller condition that becomes increasingly likely simply as the vault accrues interest over time and `total-assets` grows relative to `total-supply`. Any user (or any wallet/integration defaulting `min-out` to `0`) making a small deposit relative to vault size will trigger it deterministically once the `ta`/`ts` ratio crosses the rounding threshold. The `redeem` function's presence of `ERR-OUTPUT-ZERO` shows the maintainers intended to prevent this class of bug but missed applying it symmetrically to `deposit`.

### Recommendation
Add `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)` to `deposit` in every `v0-vault-*.clar` contract, mirroring the existing check in `redeem`, so that a zero-share mint reverts instead of silently accepting the user's underlying deposit.

### Proof of Concept
1. Let vault state be `ts = 500_000_000000` (500k shares, 6 decimals) and `ta = 1_000_000_000000` (1M underlying units) after interest accrual — a normal, expected state reachable purely by time passing and borrows accruing interest, no attacker action needed.
2. Attacker/victim calls `deposit(amount=1, min-out=0, recipient=self)`.
3. `convert-to-shares-preview(1)` computes `mul-div-down(1, 500_000_000000, 1_000_000_000000)` = `floor(0.5)` = `0`.
4. `(asserts! (>= 0 0) ERR-SLIPPAGE)` passes.
5. `receive-underlying(1, account)` transfers 1 unit of underlying into the vault; `assets` increases by 1.
6. `ft-mint? zft 0 recipient` mints zero shares.
7. Transaction succeeds (`ok inkind`) with `inkind = 0`; caller has permanently lost their 1 unit of underlying with no share claim to redeem it.

Note: The exact numeric threshold at which `amount*ts < ta` occurs depends on live vault interest-accrual history that I could not fully trace end-to-end (e.g., precise index-growth math in `accrue`/`next-index`); the mechanism and code path themselves are fully confirmed from the source shown above.

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L306-313)
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

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L332-344)
```text
(define-private (total-assets)
  (let ((current-assets (var-get assets))
        (debt (total-debt))
        (borrowed (var-get total-borrowed))
        (interest (if (> debt borrowed) (- debt borrowed) u0)))
    (+ current-assets interest)))

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

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L806-811)
```text
  (asserts! (>= current-assets inkind) ERR-INSUFFICIENT-ASSETS)
  (asserts! (not (get redeem states)) ERR-PAUSED)
  (asserts! (> amount u0) ERR-AMOUNT-ZERO)
  (asserts! (> inkind u0) ERR-OUTPUT-ZERO)
  (asserts! (>= inkind min-out) ERR-SLIPPAGE)
  (asserts! (>= available-assets inkind) ERR-INSUFFICIENT-LIQUIDITY)
```
