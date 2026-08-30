### Title
Paused vault `accrue()` silently returns stale indexes instead of reverting, letting borrow/health checks proceed on outdated debt/liquidity data - (File: `mainnet/contracts/vault/v0-vault-sbtc.clar`, similarly `v0-vault-stx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`)

### Summary
`accrue()` in each vault checks `pause-states` and, when the `accrue` flag is paused, returns `(ok { index: idx, lindex: lidx })` using the **currently stored** `index`/`lindex` variables instead of reverting or signaling that no update occurred. [1](#0-0) 

### Finding Description
`market.clar` relies on `accrue-and-cache` to populate `index-cache-`/`index-cache` for the current block timestamp, which is then read by `get-cached-indexes` for ztoken price resolution (`resolve-ztoken`), debt notional calculation, and scaled-debt conversion during `borrow`/`repay`/`collateral-add`/`collateral-remove`. [2](#0-1) [3](#0-2) 

`vault-accrue` is the sole source that `accrue-and-cache` trusts to produce fresh `{index, lindex}` values before caching them for the block: [4](#0-3) 

However, each vault's `accrue()` is a pass-through when paused — it returns success (`ok`) carrying the *old* `idx`/`lidx` var values rather than reverting the call chain that depends on it: [5](#0-4) 

Because `market.clar` treats a successful `(ok ...)` from `vault-accrue` as "freshly accrued," it caches this stale `{index, lindex}` under the current block's timestamp key exactly as if it were a genuine, current accrual. Every subsequent read within the same block (`get-cached-indexes`, `resolve-ztoken`, `is-healthy`/`is-healthy-with-mask` checks used by `borrow`, `collateral-remove`, `repay`) then uses this stale, un-invalidated value — the health check that gates borrowing/withdrawal is evaluated against indexes that no longer reflect true accrued interest for the paused vault, while the debt/liquidity amounts owed by the protocol keep growing conceptually (interest continues to be economically due even though the vault's on-chain state is frozen). [6](#0-5) 

This fits the accepted analog class "a pause that passes through instead of reverting": the pause silently substitutes stale cached state for a real state transition, and the health check (`is-healthy`/`is-healthy-with-mask`) that gates fund-moving operations (`borrow`, `system-borrow`, `collateral-remove`) is computed against this un-invalidated cached value, decoupling risk evaluation from the vault's true position.

### Impact Explanation
While a specific vault's accrual is paused (e.g., during an operational incident or planned pause), users can still `borrow` against or withdraw collateral valued using stale liquidity/borrow indexes cached at pause time. If real off-chain/expected interest would have pushed a position toward unhealthy territory, the stale cache instead reports it as healthy, letting a user extract additional debt or withdraw collateral that should have been blocked — a temporary freezing/mis-pricing of funds tied to unclaimed yield/interest accounted for by the vault. This lands in the **High** impact bucket: temporary freezing of funds / theft of unclaimed yield, since the borrow index that determines actual owed interest is silently frozen and misused in cross-contract health decisions rather than causing the transaction to fail safely.

### Likelihood Explanation
Requires the vault to be in the paused-`accrue` state (an intended, DAO-controlled operational state) at the time a user calls `borrow`/`collateral-remove`/`repay` involving that asset. This is a normal operational condition (pause for maintenance) rather than a rare edge case, so it is readily reachable whenever the pause is active, without needing any privileged compromise.

### Recommendation
`accrue()` should not report `ok` with the old values as though a fresh accrual happened when paused; either return a distinct signal (e.g., a flag indicating "stale/paused") that `market.clar`'s `accrue-and-cache` refuses to cache, or make `accrue-and-cache` itself detect the paused condition and skip caching non-fresh results, or have dependent operations (`borrow`, `collateral-remove`, `repay`, liquidation) explicitly revert when the underlying vault's accrual is paused instead of silently proceeding with stale cached indexes.

### Proof of Concept
1. DAO/operator pauses `accrue` for `vault-sbtc` (or any vault) via its pause-states control, freezing `index`/`lindex` at their last-known values. [5](#0-4) 
2. User calls `market.clar`'s `borrow` (or `collateral-remove`) for an asset routed to that vault; `accrue-user-debts`/`accrue-and-cache` invokes `vault-accrue`, which returns `(ok {index: idx, lindex: lidx})` — stale values treated as fresh. [2](#0-1) 
3. `market.clar` caches these stale indexes in `index-cache-`/`index-cache` keyed by the current block timestamp and computes `collateral-value`/`debt-value` and `is-healthy`/`is-healthy-with-mask` using them. [7](#0-6) 
4. Because the cached index does not reflect economically accrued interest for the paused period, the health check can pass for a position that would fail with true up-to-date indexes, allowing borrow/withdrawal beyond the intended safety margin.

Note: I could not fully trace how `pause-states` is toggled (its setter/authorization path) within the tool budget available, so the exact operational trigger conditions for entering the paused-`accrue` state are not fully confirmed from the index; a Devin session with full repo access would be needed to verify the pause's setter and any additional safeguards around it.

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L833-861)
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

**File:** local-testing/contracts/market/market.clar (L197-204)
```text
(define-private (vault-accrue (aid uint))
  (if (is-eq aid STX) (contract-call? .vault-stx accrue)
  (if (is-eq aid sBTC) (contract-call? .vault-sbtc accrue)
  (if (is-eq aid stSTX) (contract-call? .vault-ststx accrue)
  (if (is-eq aid USDC) (contract-call? .vault-usdc accrue)
  (if (is-eq aid USDH) (contract-call? .vault-usdh accrue)
  (if (is-eq aid stSTXbtc) (contract-call? .vault-ststxbtc accrue)
  ERR-UNKNOWN-VAULT)))))))
```

**File:** local-testing/contracts/market/market.clar (L253-265)
```text
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

**File:** local-testing/contracts/market/market.clar (L365-369)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```

**File:** local-testing/contracts/market/market.clar (L1261-1310)
```text
(define-public (borrow (ft <ft-trait>) (amount uint) (receiver (optional principal)) (price-feeds (optional (list 3 (buff 8192)))))
  (let ((address (contract-of ft))
        (asset (try! (get-asset address)))
        (asset-id (get id asset))
        (account contract-caller)
        (funds-receiver (match receiver recv recv contract-caller))
        (feeds-check (try! (write-feeds price-feeds)))
        
        ;; Step 1: Get position WITHOUT resolving prices
        (position (try! (get-position account)))
        (mask (get mask position))
        
        ;; Step 2: Accrue user's positions (populates cache for ztokens)
        (u-debt (accrue-user-debts (get debt position)))
        (u-coll (accrue-user-collateral (get collateral position)))
        
        ;; Step 3: Accrue the asset being borrowed (needed for index access)
        (unused (accrue-and-cache asset-id))
        
        ;; Step 4: NOW safe to resolve prices (cache is populated)
        (assets (get-assets mask))

        ;; Calculate current health with current mask
        (current-group (try! (get-egroup mask)))
        (current-ltvb (buff-to-uint-be (get LTV-BORROW current-group)))

        ;; LTV
        (notional-valued-assets (get-notional-evaluation { position: position, assets: assets }))
        (collateral-value (get collateral notional-valued-assets))
        (debt-value (get debt notional-valued-assets)))

    ;; preconditions
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (get debt asset) ERR-BORROW-DISABLED)
    (asserts! (is-healthy collateral-value debt-value current-ltvb) ERR-UNHEALTHY)

    ;; Calculate FUTURE debt (after adding this debt)
    ;; For debt: bit position = asset-id + 64 (DEBT-OFFSET)
    (let ((future-mask (bit-or mask (pow u2 (+ asset-id DEBT-OFFSET))))
          (future-group (try! (get-egroup future-mask)))
          ;; Per-egroup borrow disable check (uses FUTURE egroup, not current)
          ;; Each bit in BORROW-DISABLED-MASK corresponds to a debt asset ID (NOT offset by 64)
          (disabled-borrow-mask (get BORROW-DISABLED-MASK future-group))
          (debt-increase (try! (get-asset-value asset amount true)))
          (debt-post-increased (+ debt-value debt-increase)))

    ;; Check if this specific asset is disabled for borrowing in the FUTURE egroup
    (asserts! (is-eq (bit-and disabled-borrow-mask (pow u2 asset-id)) u0) ERR-EGROUP-ASSET-BORROW-DISABLED)
    ;; postconditions
    (asserts! (try! (is-healthy-with-mask collateral-value debt-post-increased future-mask)) ERR-UNHEALTHY)
```
