### Title
DAO can disable a collateral asset without checking open positions, stranding depositors while liquidation still seizes the same disabled collateral - ([File: mainnet/contracts/registry/v0-assets.clar])

### Summary
`disable` in the asset registry lets the DAO turn off an asset as collateral (or debt) for the whole protocol with no check for existing user positions in that asset, mirroring the reported `updateSettlementConfiguration` bug class (operator disables a feature without checking open exposure).

### Finding Description
`disable(asset, collateral)` flips the corresponding bit off in the global `bitmap` after only a DAO-auth check; it never inspects whether any user currently holds that asset as collateral or debt: [1](#0-0) 

Once disabled, `market.clar`'s user-facing collateral entry point gates on this exact enabled flag - `collateral-add` explicitly reverts with `ERR-COLLATERAL-DISABLED` when the asset's `collateral` flag (derived from the same bitmap) is false: [2](#0-1) 

Normal position resolution (`get-position`, `get-assets`) is filtered through `get-enabled-bitmap`, so once an asset's bit is cleared it is excluded from the "safe" collateral/debt mask used for everyday user operations: [3](#0-2) 

In contrast, `liquidate` deliberately bypasses the enabled/disabled distinction: it fetches the borrower's `get-full-position` using the full `MAX-U64` mask (all collaterals, not just enabled ones) and `process-collateral-asset` is explicitly written to resolve on-demand pricing for collateral that is *not* found in the enabled list, i.e. disabled collateral, and seize it: [4](#0-3) [5](#0-4) 

So the same asset-disable action produces two divergent code paths under Clarity's single-block evaluation: the user path guards on the bitmap and reverts, while the liquidation path is coded to explicitly ignore that guard and still act on the asset. This is structurally identical to the reported bug: a "settlement disabled" flag that blocks the affected users' own remedial action (closing/repaying/withdrawing) but not the adversarial action (liquidation) against them.

### Impact Explanation
A user holding a leveraged position collateralized by an asset the DAO later disables can be locked out of normal collateral management for that asset (since `ERR-COLLATERAL-DISABLED` blocks the flows gated the same way as `collateral-add`), while `liquidate`'s liquidation-specific code path continues to value and seize that same collateral via the full-mask/disabled-resolution logic shown above. This is a temporary freezing of user funds (in-scope "temporary freezing of funds" impact) combined with unfair, unavoidable liquidation exposure the user cannot mitigate through normal exit paths - the same asymmetry the external Zaros report flagged as High/Critical.

### Likelihood Explanation
Requires only a single DAO-authorized `disable` call (`try! (check-dao-auth)`), the same privilege level already used for all other asset/market configuration in this protocol, and no additional attacker action or cross-user interference - it is a pure single-transaction guard/no-guard mismatch, matching the allowed analog classes (a guard that blocks the victim's own remediation but not the adversarial path).

### Recommendation
Either (a) block `disable` when any open position still references the asset as collateral/debt, or (b) make user-initiated exit paths (withdraw/repay) for that asset check a "close-only" state (not the same enabled/disabled bit used for gating new positions) so that existing holders can always unwind, symmetric to how liquidation is already permitted to act on disabled collateral.

### Proof of Concept
1. User deposits `assetX` as collateral and borrows against it via `collateral-add`/`borrow` in `market.clar` while `assetX` is enabled as collateral.
2. DAO calls `disable(assetX, true)` in `v0-assets.clar` (`mainnet/contracts/registry/v0-assets.clar:280-304`); no check is made for the user's open position.
3. User attempts to add/withdraw `assetX` collateral; the `(asserts! (get collateral asset) ERR-COLLATERAL-DISABLED)` check in `market.clar:1049` (or the analogous withdrawal gate keyed off the same bitmap) reverts the call, preventing normal exit.
4. Price of the debt asset moves adversely; a liquidator calls `liquidate`, whose `process-collateral-asset` explicitly resolves price and seizes `assetX` even though it is disabled (`v0-4-market.clar:785-829`), confirming the user's collateral remains liquidatable while inaccessible to the user themselves.

### Citations

**File:** mainnet/contracts/registry/v0-assets.clar (L280-304)
```text
(define-public (disable (asset principal) (collateral bool))
  (let ((id (try! (get-reverse asset)))
        (final-id (buff-to-uint-be id))
        (enabled-mask (get-bitmap))
        (position (mask-pos final-id collateral))
        (updated-bitmap (bit-and enabled-mask (bit-not (pow u2 position)))))

      (try! (check-dao-auth))
      (asserts! (not (is-eq enabled-mask updated-bitmap)) ERR-NOT-ENABLED)
      (var-set bitmap updated-bitmap)
      
      (print {
        action: "asset-disable",
        caller: tx-sender,
        data: {
          asset-address: asset,
          asset-id: final-id,
          is-collateral: collateral,
          bitmap-before: enabled-mask,
          bitmap-after: updated-bitmap
        }
      })
      
      (ok true)
    ))
```

**File:** local-testing/contracts/market/market.clar (L1043-1050)
```text
(define-public (collateral-add (ft <ft-trait>) (amount uint) (price-feeds (optional (list 3 (buff 8192)))))
  (let ((ft-address (contract-of ft))
        (asset (try! (get-asset ft-address)))
        (asset-id (get id asset))
        (account contract-caller))

    (asserts! (get collateral asset) ERR-COLLATERAL-DISABLED)
    (asserts! (is-eq contract-caller tx-sender) ERR-AUTHORIZATION)
```

**File:** mainnet/contracts/market/v0-4-market.clar (L454-492)
```text
(define-private (get-enabled-bitmap)
  (contract-call? .v0-assets get-bitmap))

(define-private (get-status-multi (ids (list 64 uint)))
  (contract-call? .v0-assets status-multi ids))

(define-private (get-egroup (mask uint))
  (contract-call? .v0-egroup resolve mask))

(define-private (get-account-scaled-debt (account principal) (asset-id uint))
  (contract-call? .v0-market-vault get-account-scaled-debt account asset-id))

(define-private (get-position (account principal)) ;; enabled only
  (let ((mask (get-enabled-bitmap)))
    (contract-call? .v0-market-vault get-position account mask)))

(define-private (get-full-position (account principal)) ;; all collaterals
  (contract-call? .v0-market-vault get-position account MAX-U64))

(define-private (get-liquidation-position (account principal)) ;; liquidation specific (enabled collateral + all debt)
  (let ((mask (get-enabled-bitmap)))
    (contract-call? .v0-market-vault get-position account mask)))

;; -- Context & asset helpers ------------------------------------------------

(define-private (get-asset (asset principal))
  (contract-call? .v0-assets get-asset-status asset))

(define-private (get-assets (mask-user uint))
  (let ((mask-enabled (get-enabled-bitmap))
        (safe-mask (user-safe-mask mask-user mask-enabled))
        (iter (mask-to-list-collateral safe-mask))
        (assets-list (get-status-multi iter))
        (oracles-list (map get-oracle assets-list))
        ;; Extract asset-ids for price resolution
        (asset-ids (map get-asset-id assets-list))
        ;; Use internal price resolution
        (prices-list (unwrap-panic (price-multi-resolve oracles-list asset-ids))))
    (map merge-price assets-list prices-list)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L785-829)
```text
;; Process collateral asset for liquidation
;; Handles both enabled and disabled collateral assets
;; Calculates expected collateral, caps at user balance
;; Returns: { coll-actual: uint, coll-expected: uint, coll-price: uint, coll-decimals: uint }
(define-private (process-collateral-asset
  (coll-aid uint)
  (debt-actual-usd uint)
  (liq-penalty uint)
  (user-coll-balance uint)
  (assets (list 64 {
    id: uint, addr: principal, decimals: uint,
    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
    collateral: bool, debt: bool, price: uint
  }))
  (coll-asset {
    id: uint, addr: principal, decimals: uint,
    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
    collateral: bool, debt: bool
  }))
  
  (let (;; Calculate expected collateral in USD (with penalty bonus for liquidator)
        (coll-usd-expected (calc-liq-collateral-repay debt-actual-usd liq-penalty))
        
        ;; Handle disabled collaterals by resolving price if not in enabled assets
        (coll-asset-info (match (find-asset coll-aid assets)
                           ;; Found in enabled list: use it (already has price)
                           found found
                           ;; Not found (disabled): resolve price on demand
                           (let ((oracle-data (get oracle coll-asset))
                                 (price (unwrap-panic (price-resolve oracle-data))))
                             (merge coll-asset { price: price }))))
        (coll-price (get price coll-asset-info))
        (coll-decimals (get decimals coll-asset-info))
        (coll-expected (mul-div-down coll-usd-expected (pow u10 coll-decimals) coll-price))
        
        ;; cap at available collateral (user may not have enough)
        (coll-actual (if (> coll-expected user-coll-balance)
                         user-coll-balance
                         coll-expected)))
    {
      coll-actual: coll-actual,
      coll-expected: coll-expected,
      coll-price: coll-price,
      coll-decimals: coll-decimals
    }))
```
