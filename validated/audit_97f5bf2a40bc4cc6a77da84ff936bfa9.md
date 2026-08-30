### Title
`socialize-debt` writes down the vault share index using a stale, un-accrued `index`/`lindex` — (File: `mainnet/contracts/vault/v0-vault-stx.clar`)

### Summary
Every state-mutating vault entrypoint that depends on the borrow index or liquidity index (`deposit`, `redeem`, `system-borrow`, `system-repay`, `flashloan`) begins by calling `(try! (accrue))` to bring `index`/`lindex` up to date before using them. [1](#0-0) [2](#0-1)  `socialize-debt`, which is invoked by the market contract to write off bad debt after a liquidation leaves no collateral, is the one sibling function that skips this step and reads `index`/`lindex` directly with `var-get`, without refreshing them first. [3](#0-2) 

### Finding Description
`socialize-debt` computes:
- `debt-reduction` from the stale `idx = (var-get index)` [4](#0-3) 
- `old-total-assets` from `(total-assets)`, which is itself derived from the stale `lindex` value that has not been advanced by `accrue`
- a write-down `new-lindex`, proportional to `debt-reduction` relative to `old-total-assets`, then commits it with `(var-set lindex new-lindex)`. [5](#0-4) 

If interest has accrued since `last-update` but has not yet been rolled into `index`/`lindex` (i.e., the normal `accrue` step was skipped here unlike every other mutating entrypoint), `debt-reduction` and `old-total-assets` both understate the true debt/asset state at the moment of socialization. The bad-debt write-down to `lindex` is therefore computed against the wrong baseline and under-corrects the share index. This is the same class of bug as the report: a value (`index`/`lindex`) that is normally refreshed/invalidated before use in sibling operations is instead consumed stale in one operation that mutates the accounting state, permanently baking in an inconsistency rather than reverting or refreshing first.

Because the corrupted `lindex` is written directly to persistent contract state (not a local/preview value), the error is not self-correcting: the next `accrue()` call (in `deposit`/`redeem`/etc.) computes `next-liquidity-index()` starting from this already-wrong `lindex`, propagating the miscalculation forward rather than fixing it. `redeem`'s `convert-to-assets-preview` is subsequently derived from this same `lindex`, so the vault's advertised backing per share becomes inflated relative to the real remaining assets after the loss.

### Impact Explanation
Since the loss from bad debt is under-recognized in the share index, LP token holders/redeemers can subsequently withdraw more underlying assets per share than the vault actually retains post-loss-socialization, which is either a theft of funds from the remaining vault backing or a protocol insolvency condition (vault liabilities exceed available assets) — both Critical-impact classes per the specified taxonomy.

### Likelihood Explanation
This triggers on the standard bad-debt path (a liquidation that leaves the borrower with no remaining collateral), which is a routine occurrence in undercollateralized-position liquidations, not an adversarial edge case requiring privileged access. Any liquidation that leaves zero collateral for a position with unaccrued interest since the vault's `last-update` will exercise this code path.

### Recommendation
Have `socialize-debt` call `(try! (accrue))` first, exactly as `deposit`, `redeem`, `system-borrow`, `system-repay`, and `flashloan` do, so `index`/`lindex` are current before `debt-reduction` and the `lindex` write-down are computed.

### Proof of Concept
1. Time passes such that pending interest has accrued on the vault's `principal-scaled` but `accrue` has not yet been called (i.e., `index`/`lindex` are stale relative to `last-update`/`stacks-block-time`).
2. A borrower's position becomes liquidatable and is liquidated down to zero remaining collateral; `market.clar`'s `liquidate` triggers the "no collateral left" bad-debt path, calling the vault's `socialize-debt` with the outstanding scaled debt. [6](#0-5) 
3. `socialize-debt` computes `debt-reduction`/`old-total-assets` from the stale, un-accrued `index`/`lindex`, and commits an under-corrected `new-lindex` to state via `var-set lindex new-lindex`. [5](#0-4) 
4. Any subsequent `redeem` call uses this now-inflated `lindex` (via `convert-to-assets-preview`), letting share holders extract more underlying value than the vault's real post-loss backing supports.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L861-868)
```text
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))

(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L903-908)
```text
  (let (
        (states (var-get pause-states))
        (u (try! (accrue)))
        (scaled-principal (var-get principal-scaled))
        (idx (var-get index))
        (debt (total-debt))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L944-967)
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

```

**File:** mainnet/contracts/market/v0-4-market.clar (L1549-1560)
```text
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
