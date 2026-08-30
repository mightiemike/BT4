### Title
Vault `accrue` pause silently skips interest accrual instead of reverting, letting borrow/repay/liquidation proceed on a stale index - (File: `mainnet/contracts/vault/v0-vault-stx.clar` and sibling vaults)

### Summary
Every mainnet vault (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`) implements `accrue` so that when the `accrue` flag in `pause-states` is set, the function does not revert — it silently returns the currently-stored `index`/`lindex` without recomputing interest, while every other caller (`redeem`, `system-borrow`, and the market's liquidation flow) treats this as a normal, successful accrual and continues execution using the stale index.

### Finding Description
`accrue` reads `pause-states` and branches: [1](#0-0) 

If `(get accrue states)` is true, the function returns `(ok { index: idx, lindex: lidx })` — the pre-existing values — instead of asserting/reverting. This is the pause-passthrough pattern explicitly called out as an allowed analog mechanism: a pause that passes through instead of reverting.

This `accrue` result is unconditionally consumed by state-changing entry points that assume accrual actually happened: [2](#0-1) 

`system-borrow` calls `(try! (accrue))` at the top of its `let`, then immediately reads `(var-get index)` for `scaled-amount` computation — if `accrue` was paused, `idx` is the old, un-updated index, so newly-scaled borrow amounts are computed against a rate that has not captured interest that should have accrued since `last-update`. The same stale-index problem propagates into the market's liquidation path, which calls the vault's accrual (`vault-accrue`/`accrue-user-debts`/`accrue-user-collateral`) before reading `get-cached-indexes` to compute `borrow-index` for `scale-debt-for-liquidation`: [3](#0-2) [4](#0-3) 

Because the vault's `accrue` pause silently no-ops rather than reverting, the market has no way to detect that accrual didn't happen: `get-cached-indexes` is populated with the stale `borrow-index`, and `scale-debt-for-liquidation` computes `scaled-debt`/`debt-to-repay` off that stale index, meaning the accrued interest owed by the position (and hence the accrued interest owed to depositors) during the paused window is silently dropped from every repay/borrow/liquidation calculation. The pause was clearly designed as a safety knob for the vault operator, but instead of stopping dependent flows it makes them proceed on incorrect economic state, exactly mirroring the reported bug class where a `whenNotPaused` guard blocks a dependency (`redeemRewards`) that a higher-level flow (`_slash`) unconditionally relies on — here the roles are reversed (pause silently succeeds instead of reverting) but the root cause is the same: a pause boolean is not correctly threaded through to every consumer of the value it protects, so downstream logic silently operates on inconsistent/stale state.

### Impact Explanation
While `accrue` is paused, any borrow, repay, or liquidation executed against that vault computes amounts using an index that has not captured interest owed to LPs/depositors for that period. This is a temporary freezing of unclaimed yield: interest that should accrue to the vault's `total-assets`/index while paused is never captured once operations resume on the correct index unless a subsequent unpaused `accrue` call reconciles the gap — and since `last-update` also is not advanced while paused, the loss window is silent and undetectable from within the pausing operator's perspective. This lands in the in-scope "High" impact bucket: temporary freezing of unclaimed yield.

### Likelihood Explanation
Likelihood is moderate: it requires the vault operator to pause `accrue` on a specific vault (a normal, intended operational action, not privileged-key compromise) while borrow/repay/liquidation traffic continues on that vault, which is the default expected state of a pause used as an emergency brake rather than a full-operation halt. No malicious actor coordination or cross-user interference is needed — a single paused-vault administrative action combined with a single subsequent transaction against the market (a self-contained interleaving) is sufficient to produce the stale-index effect.

### Recommendation
Make the `accrue` pause behave consistently with other pause gates in the vault (i.e., `asserts! (not (get accrue states)) ERR-PAUSED`) rather than silently returning stale state, or explicitly propagate an "accrual-paused" signal to the market so that dependent flows (`system-borrow`, `liquidate`, `repay`) can decide to halt or account for it instead of silently trusting a non-refreshed index.

### Proof of Concept
1. Vault operator pauses the `accrue` flag on `v0-vault-stx.clar` via `pause-states` (a normal operational pause, not a key compromise).
2. Time (and blocks) pass; interest that should accrue to `index`/`lindex` is not applied because `accrue` returns the old cached values instead of reverting: [5](#0-4) 
3. A user calls `market.liquidate` (or `repay`/`system-borrow`) against a position collateralized/debt-denominated in the STX vault; the market calls the vault's accrual, which silently no-ops, then reads `get-cached-indexes` for `borrow-index` and computes `scale-debt-for-liquidation` off the stale index: [6](#0-5) 
4. The debt repaid/scaled-to-remove amount is computed using an index that omits interest accrued during the pause window, permanently understating the interest captured for that period once the vault later resumes normal accrual, freezing that yield from ever being distributed to depositors.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L835-845)
```text
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L865-876)
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
      (updated-scaled-principal (+ scaled-principal scaled-amount)))

```

**File:** mainnet/contracts/market/v0-4-market.clar (L858-877)
```text
(define-private (scale-debt-for-liquidation
  (debt-final uint)
  (coll-actual uint)
  (curr-scaled uint)
  (asset-id uint))
  (let (;; convert debt amount to scaled units for storage
        (borrow-index (get index (unwrap-panic (get-cached-indexes asset-id))))
        (scaled-debt (mul-div-down debt-final INDEX-PRECISION borrow-index))
        ;; cap at current debt (prevent over-repayment)
        (scaled-to-remove (if (> scaled-debt curr-scaled) curr-scaled scaled-debt))
        (debt-to-repay (mul-div-up scaled-to-remove borrow-index INDEX-PRECISION))
        ;; If debt was capped, scale collateral proportionally
        (coll-final (if (< scaled-to-remove scaled-debt)
                        (mul-div-down coll-actual scaled-to-remove scaled-debt)
                        coll-actual)))
    {
      scaled-to-remove: scaled-to-remove,
      debt-to-repay: debt-to-repay,
      coll-final: coll-final
    }))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1405-1409)
```text
    ;; accrue FIRST - populates cache for zToken price resolution
    (u-debt (accrue-user-debts (get debt pos-full)))
    (u-coll (accrue-user-collateral (get collateral pos-full)))

    ;; NOW safe to resolve prices (cache is populated)
```
