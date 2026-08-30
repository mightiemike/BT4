### Title
Zero-share deposit after `socialize-debt` write-off lets deposits be silently donated to existing zft holders - ([File: mainnet/contracts/vault/v0-vault-sbtc.clar])

### Summary
Each Zest v2 vault (`v0-vault-sbtc.clar`, `v0-vault-stx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, and their `local-testing` counterparts) computes shares to mint for a `deposit` via `convert-to-shares-preview`, which special-cases only the "no assets ever deposited" case (`ts == u0`). When total shares (`ts`) are still outstanding but total vault assets (`ta`) have been driven to zero — which happens through the vault's own `socialize-debt` bad-debt write-off path — the function returns `u0` shares for any deposit amount, while the deposit still pulls in the depositor's underlying tokens. This is the same root cause as the reported "Slashing-Induced Share Dilution" bug (share-price collapse to zero when the underlying balance is wiped while shares remain outstanding), reachable in-protocol via the market's bad-debt socialization flow rather than an external slashing instruction.

### Finding Description
`convert-to-shares-preview` in the vault contracts:
```
(define-private (convert-to-shares-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ts u0)
        amount
        (if (is-eq ta u0)
            u0
            (mul-div-down amount ts ta)))))
``` [1](#0-0) 

only guards the *first-depositor* edge case (`ts == u0`). It does **not** guard the inverse case where shares are outstanding (`ts > 0`) but total assets have fallen to zero (`ta == u0`) — in that branch it returns `u0` unconditionally.

Total vault `assets` can be driven to `u0` through the market's bad-debt socialization flow, which calls the vault's `socialize-debt` when a liquidation leaves no collateral to recover:
```
(var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))
``` [2](#0-1) 
called from `socialize-debt-asset` / `liquidate` in the market contract when bad debt is written off: [3](#0-2) 

This is an in-protocol equivalent of the external report's "slashing": the vault's underlying balance is legitimately reduced to zero (full bad-debt write-off) while `zft` (the vault's share token) supply remains unchanged, because share holders have not yet redeemed.

Once this state exists, the `deposit` entry point uses the broken preview to compute shares to mint:
```
(inkind (convert-to-shares-preview amount)))
...
(asserts! (>= inkind min-out) ERR-SLIPPAGE)
...
(try! (receive-underlying amount account))
(try! (ft-mint? zft inkind recipient))
(var-set assets (+ current-assets amount))
``` [4](#0-3) 

Because `inkind` is `u0` in this state, the slippage guard `(asserts! (>= inkind min-out) ERR-SLIPPAGE)` only reverts if the caller explicitly sets `min-out > 0`. A depositor calling `deposit` with the common default `min-out = 0` (or any caller/integration that doesn't set slippage protection because it expects a >0 mint under normal operation) has their `amount` of underlying tokens pulled into the vault (`receive-underlying`), `ft-mint? zft u0 recipient` mints them nothing, and `assets` is increased by their contribution — which simply restores value to the pre-existing (impaired) `zft` supply. The depositor's funds are effectively donated to existing `zft` holders with no compensating shares, mirroring exactly the dynamic described in the external report.

### Impact Explanation
This results in permanent loss/freezing of the new depositor's funds: they contribute real underlying tokens and receive zero vault shares in return, with no path to recover their contribution (it is absorbed into `assets`, benefiting only existing `zft` holders). This is a direct, protocol-native (not third-party-oracle-caused) mechanism reachable through the ordinary liquidation → bad-debt-socialization flow, not requiring DAO compromise or privileged access. It qualifies as Critical/High impact: permanent freezing (effectively loss) of user funds deposited into an impaired vault.

### Likelihood Explanation
Requires a vault to first reach a state where `total-assets-preview` is `u0` while `zft` supply remains `>0` — i.e., a liquidation whose bad debt fully consumes the vault's tracked assets via `socialize-debt`, which is a normal (if rare/stressed-market) path already present in the liquidation logic, not a hypothetical or externally injected slashing event. It further requires a subsequent depositor to call `deposit` without a protective `min-out`, which is plausible for integrators/UI flows using default parameters or naive slippage settings.

### Recommendation
In `convert-to-shares-preview`, when `ta == u0` and `ts > 0` (assets wiped but shares outstanding), either revert the deposit entirely (return an error / disallow deposits) rather than silently returning `u0` shares, or otherwise re-base share issuance so that depositors are not diluted to zero. At minimum, `deposit` should explicitly reject `inkind == u0` (similar to the `ERR-OUTPUT-ZERO` check already present in `redeem`) instead of relying solely on caller-supplied `min-out` slippage protection.

### Proof of Concept
1. A borrower's collateral becomes worthless / insufficient; `liquidate` in the market contract removes all collateral and, finding `no-collateral-left`, calls `socialize-debt-asset` → vault's `socialize-debt`, reducing `var-get assets` to `u0` for a vault (e.g. `v0-vault-sbtc`) while `zft` total supply remains `>0` (existing depositors have not redeemed).
2. `total-assets-preview` for that vault now returns `u0`; `total-supply-preview` returns the outstanding `zft` supply (`> u0`).
3. A user calls `deposit(amount, min-out=0, recipient)` on the impaired vault.
4. `convert-to-shares-preview(amount)` evaluates the `(is-eq ta u0)` branch and returns `inkind = u0`.
5. `(asserts! (>= inkind min-out) ERR-SLIPPAGE)` passes because `u0 >= u0`.
6. `receive-underlying amount account` pulls the depositor's tokens into the vault; `ft-mint? zft u0 recipient` mints zero shares; `var-set assets (+ current-assets amount)` credits the vault's assets, benefiting existing `zft` holders' redeemable value.
7. The depositor has irrecoverably lost `amount` of underlying tokens for zero `zft` shares. [1](#0-0) [4](#0-3) [5](#0-4) [3](#0-2)

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

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L761-793)
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

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L942-968)
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

    (var-set lindex new-lindex)
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))

    (print {
      action: "socialize-debt",
      caller: contract-caller,
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1534-1560)
```text
      ;; Handle bad debt socialization if no collateral left
      (let ((bad-debt-socialized 
              (if no-collateral-left
                  (let ((stripped-debt-list (filter-out-debt-asset (get debt pos-full) debt-aid))
                        (fresh-debt-list (if (is-eq debt-updated u0)
                                             stripped-debt-list
                                             (unwrap-panic (as-max-len?
                                               (append stripped-debt-list
                                                       { aid: debt-aid, scaled: debt-updated })
                                               u64)))))
                    (if (> (len fresh-debt-list) u0) ;; if still has debt
                      (let ((socialization-result (fold socialize-debt-asset 
                                                        fresh-debt-list 
                                                        { borrower: borrower, success: true })))
                        (asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)
                        ;; emit bad-debt-socialized event
                        (print {
                          action: "bad-debt-socialized",
                          caller: contract-caller,
                          data: {
                            borrower: borrower,
                            debt-list: fresh-debt-list
                          }
                        })
                        true)
                      false))
                  false)))
```
