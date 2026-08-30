### Title
Depositors can front-run bad-debt socialization to redeem at the pre-write-down share price - (File: `mainnet/contracts/vault/v0-vault-sbtc.clar` and equivalent vault contracts)

### Summary
Zest's vaults (`v0-vault-sbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, etc.) implement the exact share-pool mechanic described in the Hubble `InsuranceFund` report: depositors hold pool shares (`zft`) whose redemption value is `assets / totalSupply`, and a public, permissionless entry point (`market.clar`'s `liquidate()`) can trigger `socialize-debt`, which slashes `assets` (and `lindex`) for every remaining shareholder when a position's collateral cannot cover its debt.

### Finding Description
When a position has no collateral left after liquidation, `liquidate()` in `mainnet/contracts/market/v0-4-market.clar` calls `socialize-debt-asset`, which invokes `vault-socialize-debt` → the vault's public `socialize-debt` function [1](#0-0) . That function reduces `total-borrowed`, `principal-scaled`, and critically `assets` and writes down `lindex` proportionally to the loss, exactly mirroring the `InsuranceFund.pendingObligation`/`balance()` write-down pattern from the original report [2](#0-1) .

Any depositor holding `zft` shares can call the vault's `redeem()` function directly, which computes the payout as `inkind = convert-to-assets-preview(amount)` against the *current* (pre-write-down) `assets`/`lindex` state, then burns shares and pays out `inkind` before any socialization has occurred [3](#0-2) . `liquidate()` in the market contract is fully public/permissionless — anyone (a liquidator) can trigger the bad-debt-creating transaction, and its outcome (whether `no-collateral-left` becomes true and bad debt gets socialized) is visible/predictable to any depositor watching pending liquidatable positions [4](#0-3) .

This is structurally identical to the reported bug class: a shared-pool `withdraw()`/`redeem()` that is not blocked or re-priced by a pending obligation, allowing a holder to exit at the stale, pre-loss exchange rate by transacting ahead of the socialization event, shifting the entire loss onto shareholders who do not (or cannot) redeem in time.

### Impact Explanation
Depositors who successfully redeem ahead of `socialize-debt` extract full share value while the loss is entirely absorbed by remaining depositors' `assets` balance, i.e. remaining depositors suffer a disproportionate, permanent loss of principal (not merely "unclaimed yield") relative to what should have been shared pro-rata across all depositors at the time bad debt was recognized. This is a form of temporary/permanent freezing and unfair socialization of losses among LPs — falls under "protocol insolvency" / "permanent freezing of funds" impact for the remaining depositors, since their expected redemption value is reduced beyond what proportional socialization intended, while early-exiting depositors avoid any share of it.

### Likelihood Explanation
The trigger (`liquidate()`) is a fully public, permissionless function, and whether a liquidation will strip all collateral and force `socialize-debt-asset` is computable off-chain from public position/price data before the liquidating transaction lands, exactly as in the original report where `settleBadDebt` was a public trigger. Any vault depositor who is also capable of monitoring positions (or is the liquidator's associate) can redeem in a preceding block/transaction to escape the loss, making this practically exploitable whenever a large bad-debt event is imminent and predictable ahead of time.

### Recommendation
- Do not allow instantaneous full-price redemption once a bad debt condition is imminent/pending; e.g. introduce a pending-obligation-style guard in `redeem()` (mirroring the referenced `settlePendingObligation` pattern) that pauses or discounts redemptions while a socialization is in-flight or has been signaled.
- Alternatively, compute a "worst-case" exchange rate check pre-redemption that accounts for known unresolved bad debt before finalizing `inkind`, or require redemption requests to go through a delay window so the socialization write-down described in `socialize-debt-asset` is applied before any withdrawal can be settled at a given block's exchange rate.
- Consider having `liquidate()` "reserve" or provisionally slash `assets` earlier in its execution path (before any depositor could possibly react in the same or an adjacent block) rather than only at the final `socialize-debt-asset` step.

### Proof of Concept
1. Alice deposits into `v0-vault-sbtc` and holds `zft` shares valued via `assets`/`total-supply`.
2. Bob's collateralized position in `market.clar` becomes deeply underwater (collateral << debt) — publicly visible via oracle price and position data.
3. Any actor submits `liquidate()` on Bob's position; because `coll-removed` reaches `u0` and no collateral remains, this call path is guaranteed to invoke `socialize-debt-asset` → `vault-socialize-debt` [4](#0-3) .
4. Before that liquidation transaction is confirmed (or in an earlier position within the same block), Alice calls `redeem()` on `v0-vault-sbtc` with all her shares. `redeem()` uses the current `assets`/`lindex`, unaffected by the still-pending socialization, and pays her the pre-loss value [3](#0-2) .
5. The liquidation transaction then executes `socialize-debt`, decreasing `assets`/`lindex` for the vault [5](#0-4) ; the loss is now spread only across the depositors who did not redeem first, exactly matching the InsuranceFund `withdraw()`/`seizeBadDebt()` race in the referenced report.

### Citations

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1526-1560)
```text
          (no-collateral-left (and
                                (is-eq coll-removed u0)
                                (or
                                  (is-eq (len (get collateral pos-full)) u1)
                                  (and
                                    (is-eq (len (get collateral pos-full)) (len (get collateral position)))
                                    (is-eq other-debt-repayable u0))))))

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

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L795-829)
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

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L943-984)
```text
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
      data: {
        scaled-amount: scaled-amount,
        debt-reduction: debt-reduction,
        principal-reduction: principal-reduction,
        old-lindex: current-lindex,
        new-lindex: new-lindex,
        old-total-assets: old-total-assets,
        principal-scaled: (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0),
        total-borrowed: (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0),
        index: idx
      }
    })

    (ok true)))

;; -- Flashloan --------------------------------------------------------------
```
