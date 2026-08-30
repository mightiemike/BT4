## Title
Accrual pause silently returns stale index instead of reverting, letting borrow/repay/liquidation proceed on stale interest data - (File: `mainnet/contracts/vault/v0-vault-usdc.clar`)

### Summary
The `accrue` pause switch in every vault contract (`v0-vault-usdc.clar`, `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) is implemented as a pass-through: when accrual is paused, `accrue` returns the last stored `index`/`lindex` with an `(ok ...)` response instead of reverting, and callers in `market.clar` treat this as a normal successful accrual.

### Finding Description
`accrue` in the vaults is written as:
```
(if (get accrue states)
    ;; PAUSED: Pass-through without reverting
    (ok { index: idx, lindex: lidx })
    ;; NOT PAUSED: Normal accrual logic
    ...)
``` [1](#0-0) 

This matches the "pause that passes through instead of reverting" analog class. The market contract's `accrue-and-cache` treats whatever `vault-accrue` returns as a valid, fresh index and caches it keyed only by `{ timestamp: stacks-block-time, aid }`: [2](#0-1) 

Downstream, this cached (but stale, because accrual was paused) index is used directly for debt notional valuation, liquidation debt sizing, and scaled-debt conversion: [3](#0-2) [4](#0-3) 

Because `market.clar` never checks the vault's pause state before consuming the accrual result, and `accrue` cannot signal "accrual skipped due to pause" as distinct from "accrual succeeded, no time elapsed," any code path that assumes the returned index reflects current time-weighted interest (health checks, liquidation sizing, debt repay conversion) silently operates on outdated data whenever an admin pauses `accrue` on a vault (e.g., during an incident) while borrow/repay/liquidate remain reachable through other pause flags.

### Impact Explanation
If `accrue` is paused on a vault (a deliberate, admin-triggered single flag) while other vault operations (`borrow`, `repay`, `liquidate`) are not simultaneously paused, all debt/collateral notional math for that asset will use a frozen `index`/`lindex` for the duration of the pause. Debtors are not charged accruing interest (temporary freezing of unclaimed yield/interest for suppliers), and liquidation math computed off stale indices can under- or over-size collateral seizure relative to true outstanding debt, creating a mismatch between recorded scaled debt and real economic debt once accrual is later resumed. This lands in the "temporary freezing of funds" / "theft of unclaimed yield" impact category.

### Likelihood Explanation
Requires only a single admin action (pausing `accrue` while leaving borrow/repay/liquidate active) — this is plausible if the pause flags are intended to be used independently (there are separate pause bits per operation, as evidenced by `(get accrue states)` alongside other pause checks such as `(get redeem states)`). No multi-party interference or DAO compromise is needed; it's a single state flag whose effect silently degrades downstream calculations rather than blocking them.

### Recommendation
Make `accrue` return a distinct error (or an explicit "paused" flag in its response) instead of `(ok {...})` when accrual is paused, and have `market.clar`'s `accrue-and-cache`/`vault-accrue` callers propagate that as a hard revert (or explicitly skip caching stale data) for any code path that computes health, liquidation, or repayment amounts, rather than treating a paused accrual as a normal successful refresh.

### Proof of Concept
1. DAO/admin sets `pause-states` on `v0-vault-usdc` such that `(get accrue states)` is `true`, while borrow/repay/liquidate pause bits remain `false`.
2. A user calls `liquidate` in `v0-4-market.clar`. This calls `accrue-user-debts` → `accrue-and-cache` → `vault-accrue` (USDC).
3. `accrue` sees `(get accrue states)` is `true` and returns `(ok { index: idx, lindex: lidx })` with the OLD, non-time-adjusted index, exactly as if accrual had succeeded normally. [1](#0-0) 
4. `market.clar` caches this stale index under `index-cache` keyed by current `stacks-block-time` and uses it, without any distinguishing signal, in `calculate-asset-notional-value`, `process-debt-asset`, and `scale-debt-for-liquidation` to compute debt-USD value and scaled debt to remove. [5](#0-4) [4](#0-3) 
5. Because the index used does not reflect interest that should have accrued over elapsed time, the liquidation's debt-to-repay/collateral-to-seize amounts are computed against understated debt, and once accrual is unpaused the real debt "catches up" — leaving the position's scaled-debt bookkeeping inconsistent with what was actually collected/seized during the paused window.

### Citations

**File:** local-testing/contracts/vault/vault-sbtc.clar (L837-865)
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
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
                (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
                (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
                (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
                (treasury-lp (if (> reserve-inc u0) (mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc)) u0)))
            (if (not (is-eq idx next))
                (var-set index next)
                false)
            (if (not (is-eq lidx nliq))
                (var-set lindex nliq)
                false)
            (if (> treasury-lp u0)
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L243-257)
```text
;; -- Accrual & caching ------------------------------------------------------

(define-private (accrue-and-cache (aid uint))
  (let ((cache-key { timestamp: stacks-block-time, aid: aid })
        (cached? (map-get? index-cache cache-key)))

    (match cached?
      ;; cache HIT: return cached value (1 read only)
      cached-indexes (ok cached-indexes)

      ;; cache MISS: accrue and cache (vault-accrue now returns indexes)
      (let ((indexes (try! (vault-accrue aid))))
        ;; store in cache
        (map-set index-cache cache-key indexes)
        (ok indexes)))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L544-574)
```text
(define-private (calculate-asset-notional-value
          (asset-entry {
              id: uint, addr: principal, decimals: uint,
              oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
              collateral: bool, debt: bool, price: uint })
          (acc { clist: (list 64 { aid: uint, amount: uint }),
                  dlist: (list 64 { aid: uint, scaled: uint }),
                  coll-total: uint,
                  debt-total: uint }))
  (let ((asset-id (get id asset-entry))
        (price (get price asset-entry))
        (decimals (get decimals asset-entry))
        (collateral-list (get clist acc))
        (debt-list (get dlist acc))
        (coll-amount (find-collateral-amount collateral-list asset-id))
        (coll-notional (if (> coll-amount u0)
                           (normalize (* coll-amount price) decimals false)
                           u0))

        (debt-scaled   (find-debt-scaled debt-list asset-id))
        (debt-notional (if (> debt-scaled u0) ;; use cache instead here
                           (let ((cached (unwrap-panic (accrue-and-cache asset-id)))
                                 (ib (get index cached))
                                 (actual (mul-div-up debt-scaled ib INDEX-PRECISION)))
                             (normalize (* actual price) decimals true))
                           u0)))

    { clist: collateral-list,
      dlist: debt-list,
      coll-total: (+ (get coll-total acc) coll-notional),
      debt-total: (+ (get debt-total acc) debt-notional) }))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L761-777)
```text
(define-private (process-debt-asset
  (debt-amount uint)
  (debt-aid uint)
  (max-debt-usd uint)
  (assets (list 64 {
    id: uint, addr: principal, decimals: uint,
    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
    collateral: bool, debt: bool, price: uint
  })))
  (let ((debt-asset-info (unwrap-panic (find-asset debt-aid assets)))
        (debt-price (get price debt-asset-info))
        (debt-decimals (get decimals debt-asset-info))
        (debt-usd (normalize (* debt-amount debt-price) debt-decimals false))
        ;; cap debt at maximum liquidatable amount
        (debt-actual-usd (if (> debt-usd max-debt-usd) max-debt-usd debt-usd))
        ;; convert capped USD amount back to token amount
        (debt-actual (mul-div-down debt-actual-usd (pow u10 debt-decimals) debt-price)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L858-869)
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
```
