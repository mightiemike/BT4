## Analysis

Confirmed root cause: `socialize-debt` in every vault (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`) computes `old-total-assets` from the **non-preview** `(total-assets)` helper, which is built from the stale, unaccrued `index`/`assets` state variables, and it never calls `(accrue)` first — unlike every sibling state-mutating entrypoint (`deposit`, `redeem`, `system-borrow`, `system-repay`, `flashloan`, `transfer`), which all begin with `(try! (accrue))` before touching `total-assets`/`total-debt`. [1](#0-0) [2](#0-1) [3](#0-2) 

Compare with `system-borrow`/`system-repay`, which always call `accrue` first: [4](#0-3) [5](#0-4) 

### Title
Stale unaccrued index used in `socialize-debt` corrupts liquidity index and permanently freezes/loses supplier yield - (File: `mainnet/contracts/vault/v0-vault-stx.clar` and equivalent `v0-vault-*.clar` files)

### Summary
`socialize-debt` writes down the liquidity index (`lindex`) proportionally to `old-total-assets`, but computes `old-total-assets` from the un-accrued `(total-assets)` helper (based on stored `index`, not `next-index`) instead of calling `(accrue)` first as every other state-mutating vault entrypoint does. Because the borrow index continuously accrues interest between blocks, `old-total-assets` understates the vault's true assets (stored assets + pending, un-posted interest) at the moment of write-down.

### Finding Description
`total-assets` uses the stored `index` var directly: [6](#0-5) . The "true" (pending) value is only obtainable via `total-assets-preview`, which uses `next-index`/`debt-preview` to account for interest accrued since `last-update`: [7](#0-6) . `next-index` explicitly recomputes based on elapsed time since `last-update`: [8](#0-7) .

`socialize-debt` binds `old-total-assets` to the stale `(total-assets)` (not the preview) and never calls `(accrue)` to flush pending interest into `assets`/`index` before using it: [9](#0-8) . The write-down formula `new-lindex = current-lindex * (old-total-assets - debt-reduction) / old-total-assets` is thus computed against an undercounted denominator/base whenever this call happens more than one block after the vault's `last-update` — a routine occurrence in production since `accrue` is opportunistically triggered by whichever user transaction happens to touch the vault.

This is called from `v0-4-market.clar`'s liquidation flow via `socialize-debt-asset`, invoked whenever a liquidation leaves the borrower with no collateral and residual bad debt (`no-collateral-left` branch): [10](#0-9) [11](#0-10) . Notably, `socialize-debt-asset` calls `vault-accrue` **after** `vault-socialize-debt` (i.e., after the stale write-down already happened), confirming the ordering bug — accrual is deliberately deferred until after the corrupted write-down: [12](#0-11) .

Sequence:
1. Time passes since the vault's `last-update`; pending borrow interest exists but is not yet posted to `index`/`assets` (stacks-block-time > last-update, no intervening accrue-triggering tx).
2. A liquidation strips a borrower's last collateral, leaving bad debt; `v0-4-market.clar` calls `socialize-debt-asset`, which calls `vault-socialize-debt` on the affected vault BEFORE calling `vault-accrue`.
3. Inside the vault, `socialize-debt` computes `old-total-assets` via `(total-assets)`, which uses the stale `index`, undercounting real vault assets by the not-yet-posted interest.
4. `new-lindex` is derived from this undercounted base, over-punishing the liquidity index (all zToken holders take a larger writedown of their share value than the actual bad debt warrants — supplier yield already earned but unposted is destroyed along with the bad-debt loss).
5. Only after this, `vault-accrue` is called and posts the (now smaller, because principal-scaled was already reduced) interest, permanently baking in the shortfall — the discrepancy can never be corrected because `lindex`/`assets` have already been mutated based on the wrong baseline.

### Impact Explanation
Every zToken/vault-share holder's redeemable value (`convert-to-assets-preview`, ultimately `get-total-assets`) is derived from `lindex`/`assets`, both mutated by this miscalculation. The bug destroys already-accrued-but-unposted supplier interest beyond what the actual bad debt requires, permanently misallocating value away from suppliers — a permanent freezing/loss of unclaimed yield, matching the High-severity impact class (theft/permanent freezing of unclaimed yield).

### Likelihood Explanation
This triggers on every bad-debt liquidation where meaningful time has elapsed since the vault's last accrual — a routine, expected event (liquidations frequently happen after periods without other vault-touching transactions, and are often time-sensitive/rushed responses to price crashes, making a "someone else touched the vault recently" precondition unreliable). No privileged access or attacker-controlled timing manipulation is needed; it happens automatically as a side effect of the ordinary liquidation-with-bad-debt code path.

### Recommendation
Call `(try! (accrue))` at the start of `socialize-debt` (as is done in `deposit`, `redeem`, `system-borrow`, `system-repay`, `flashloan`), and use `total-assets`/`total-debt` only after accrual has flushed pending interest into `index`/`assets`/`last-update`. Additionally reorder `socialize-debt-asset` in `v0-4-market.clar` so `vault-accrue` runs before `vault-socialize-debt`, not after.

### Proof of Concept
Conceptual reproduction (cannot execute tests via available tools, but the arithmetic is directly derivable from the cited code):
1. Deploy vault, deposit `X` underlying, borrow `Y` against collateral to create utilization > 0.
2. Advance `stacks-block-time` by several accrual periods without calling any vault function (so `index` remains at its old value while `next-index`/`debt-preview` would report a higher, "true" debt/assets figure).
3. Trigger a liquidation that leaves the borrower under-collateralized with no collateral left, causing `v0-4-market.clar`'s `liquidate` to call `socialize-debt-asset` → `vault-socialize-debt`.
4. Observe that `old-total-assets` inside `socialize-debt` (computed via `(total-assets)`) is strictly less than what `(total-assets-preview)` would report at that same block, because pending interest hasn't been posted.
5. Compare `new-lindex` computed by `socialize-debt` against what it would have been had `accrue` run first (i.e., using `total-assets-preview` as `old-total-assets`): the un-accrued version yields a strictly lower `new-lindex`, i.e., a larger writedown than justified by the actual bad debt, at the expense of all zToken holders' claimable yield.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L328-346)
```text
(define-private (total-debt)
  (calc-cumulative-debt (var-get principal-scaled) (var-get index)))

(define-private (debt-preview)
  (calc-cumulative-debt (var-get principal-scaled) (next-index)))

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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L379-390)
```text
(define-private (next-index)
  (let ((states (var-get pause-states))
        (idx (var-get index)))
    (if (get accrue states)
        idx
        (let (
            (rate (interest-rate))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta rate time-delta true))))
          (calc-index-next idx multiplier)))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L865-874)
```text
(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      (CAP-DEBT (var-get cap-debt))
      (available-assets (get-available-assets))
      (scaled-principal (var-get principal-scaled))
      (idx (var-get index))
      (debt (total-debt))
      (scaled-amount (mul-div-up amount INDEX-PRECISION idx))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L902-910)
```text
(define-public (system-repay (amount uint))
  (let (
        (states (var-get pause-states))
        (u (try! (accrue)))
        (scaled-principal (var-get principal-scaled))
        (idx (var-get index))
        (debt (total-debt))
        (total-borrowed-amount (var-get total-borrowed))
        (capped-amount (if (> amount debt) debt amount))
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1557-1583)
```text
                        })
                        true)
                      false))
                  false)))
        
        ;; emit main liquidate event
        (print {
          action: "liquidate",
          caller: contract-caller,
          data: {
            liquidator: liquidator,
            borrower: borrower,
            collateral-asset-id: coll-aid,
            collateral-asset-addr: coll-address,
            debt-asset-id: debt-aid,
            debt-asset-addr: debt-address,
            debt-repaid: debt-to-repay,
            debt-repaid-usd: debt-final-usd,
            collateral-seized: coll-final,
            collateral-price: coll-price,
            collateral-decimals: coll-decimals,
            liq-penalty-bps: liq-penalty,
            position-collateral-usd-before: total-collateral-usd,
            position-debt-usd-before: total-debt-usd,
            bad-debt-socialized: bad-debt-socialized
          }
        })
```
