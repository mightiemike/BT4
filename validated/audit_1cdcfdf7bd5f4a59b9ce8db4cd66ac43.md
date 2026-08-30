### Title
Depositors can dodge bad-debt socialization losses by redeeming and re-depositing around a `liquidate` call - (File: `mainnet/contracts/market/v0-4-market.clar`, `mainnet/contracts/vault/v0-vault-usdc.clar` and sibling vaults)

### Summary
`liquidate()`'s bad-debt socialization path writes down the vault's pooled `assets` variable by a fixed absolute amount, while `redeem`/`deposit` compute shares against that pooled `assets`/`total-supply` ratio with no timelock, cooldown, or snapshot protecting against being called immediately before/after the write-down. A depositor can redeem right before triggering (or observing) a liquidation that produces bad debt, let the write-down apply to a smaller remaining asset base, then redeposit the same capital to receive more shares than they started with — pushing a disproportionate share of the fixed loss onto depositors who did not exit.

### Finding Description
`socialize-debt` in the vault contracts (e.g. `mainnet/contracts/vault/v0-vault-stx.clar` and identical logic in `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`) subtracts a fixed `principal-reduction` from the pooled `assets` variable: [1](#0-0) 

This is invoked from `market`'s liquidation flow when a position's collateral is exhausted, via `socialize-debt-asset` → `vault-socialize-debt`: [2](#0-1) [3](#0-2) 

Meanwhile `redeem` and `deposit` price shares purely off the current `assets`/`total-supply` ratio at call time, with no lock preventing a depositor from calling `redeem` immediately before a liquidation and `deposit` immediately after within the same transaction/block: [4](#0-3) 

Because the loss subtracted by `socialize-debt` is a fixed absolute amount rather than one computed proportionally against whoever is invested at the moment of the loss, an actor who removes their principal just before the write-down and returns it just after receives more shares back than if they had stayed invested, while the loss is absorbed by a smaller remaining asset base — increasing the percentage loss for depositors who remained. This mirrors the reported bug class (dodging a "redistributed" loss by exiting/re-entering around the loss-triggering event), except it targets the *vault* depositors (lenders), which is where bad debt actually lands in this architecture, rather than other borrowers.

### Impact Explanation
This allows a single actor to extract value from passive vault depositors without taking on any of the bad-debt loss they would otherwise be due, directly transferring funds away from other users at rest. This is a direct theft of user funds (Critical impact class).

### Likelihood Explanation
The actor needs to: (1) hold a redeemable vault position, (2) observe or self-trigger an under-collateralized `liquidate()` call that results in `bad-debt-socialized: true`, and (3) call `redeem` then `deposit` around it. Since `liquidate` is permissionless and callable by anyone (including the attacker), and `redeem`/`deposit` have no cooldown or block-based restriction, the entire sequence can be composed atomically by a single contract-call transaction or executed opportunistically across adjacent transactions in the same block whenever a known bad-debt liquidation is about to occur. No special privileges or unrelated third parties are required, making this feasible for any sufficiently large depositor.

### Recommendation
Snapshot vault share price accrual (including bad-debt write-downs) so that pending/imminent socialized losses cannot be avoided by exiting just before them — e.g., apply a withdrawal cooldown/timelock on `redeem` similar to what is recommended for Trove closures in the original report, or ensure `socialize-debt` losses are charged pro-rata against `total-supply` measured before the triggering liquidation transaction begins rather than at redeem time, closing the window for same-block/same-transaction exit-and-reentry arbitrage around the write-down.

### Proof of Concept
1. Vault state: `assets = 1000`, `total-supply = 1000` (price = 1.0). Depositor D holds 100 shares (worth 100).
2. D observes an imminent `liquidate()` call on an underwater borrower that will trigger `socialize-debt-asset` → `vault-socialize-debt`, cutting a fixed `principal-reduction = 50` from `assets`. [5](#0-4) 
3. D calls `redeem(100, 0, D)` before the liquidation: burns 100 shares, receives `inkind = 100` (at price 1.0). New state: `assets = 900`, `total-supply = 900`. [6](#0-5) 
4. The liquidation executes, `socialize-debt` fires: `assets = 900 - 50 = 850`, `total-supply` unchanged at 900 (price ≈ 0.9444).
5. D calls `deposit(100, 0, D)`: `inkind (shares) = 100 * 900 / 850 ≈ 105.88` shares minted. [7](#0-6) 
6. D's 105.88 shares at the post-deposit price (~0.9444) are worth ~100 — D bears 0% of the loss — while the depositors who stayed with the remaining 800 shares now absorb the 50 loss against a smaller 900-asset base (≈5.56% loss) instead of the ≈5% loss they would have faced had D not exited and re-entered.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L944-970)
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

**File:** mainnet/contracts/market/v0-4-market.clar (L879-903)
```text
(define-private (socialize-debt-asset
                (debt-entry { aid: uint, scaled: uint })
                (acc { borrower: principal, success: bool }))
  ;; Early return if previous socialization failed
  (if (not (get success acc))
      acc
      (let ((borrower (get borrower acc))
            (failed-status { borrower: borrower, success: false })
            (asset-id (get aid debt-entry))
            (scaled-debt (get scaled debt-entry)))

            ;; Socialize in vault - pass scaled directly to avoid rounding
            (unwrap! (vault-socialize-debt asset-id scaled-debt) failed-status)
            ;; Refresh cache with new indexes post-write-down (lindex decreased)
            (map-set index-cache
                     { timestamp: stacks-block-time, aid: asset-id }
                     (unwrap! (vault-accrue asset-id) failed-status))
            ;; Remove from obligation
            (unwrap! (contract-call? .v0-market-vault
                                      debt-remove-scaled
                                      borrower
                                      scaled-debt
                                      asset-id) failed-status)
          acc)
        ))
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

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L761-829)
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

  (try! (ft-burn? zft amount account))
  (try! (send-underlying inkind recipient))
  (var-set assets (- current-assets inkind))

  (print {
    action: "redeem",
    caller: contract-caller,
    data: {
      redeemer: account,
      recipient: recipient,
      shares-burned: amount,
      amount-received: inkind,
      assets: (- current-assets inkind)
    }
  })

  (ok inkind)))
```
