### Title
Stale accrual-pause index cached and reused for debt/collateral pricing after unpause within the same block - (File: `mainnet/contracts/vault/v0-vault-stx.clar`, `mainnet/contracts/market/v0-4-market.clar`)

### Summary
When a vault's accrual is paused, `accrue()` returns the current `index`/`lindex` unchanged instead of reverting or reflecting real state, and this pass-through value gets written into `market.clar`'s block-scoped `index-cache`. If accrual is unpaused later in the same block, the vault's real index jumps to catch up on the frozen elapsed time, but `market.clar`'s cache is never invalidated for that timestamp, so subsequent operations in the same block read the stale pre-jump index for debt/collateral math.

### Finding Description
`accrue()` in every vault contract explicitly passes through without reverting when accrual is paused: [1](#0-0) 

While paused, `last-update` is never advanced (it is only set inside the "NOT PAUSED" branch): [2](#0-1) 

`market.clar`'s `accrue-and-cache` treats whatever `vault-accrue` (which calls `accrue`) returns as authoritative and caches it, keyed only by `{timestamp: stacks-block-time, aid}`: [3](#0-2) 

Because the cache key is purely time-based, it is never invalidated when the vault's underlying index actually changes later in the *same* block:

1. Vault accrual for asset X is paused (`accrue` flag set true) - `last-update` freezes at time T0.
2. A user calls `market.borrow`/`repay` for asset X. `accrue-user-debts`/`accrue-and-cache` calls `vault-accrue`, which hits the paused pass-through branch and returns the stale `{index, lindex}` from T0. Market caches this stale pair under `{timestamp: current-block-time, aid: X}`. [4](#0-3) 
3. Later in the *same* block, the DAO/operator unpauses accrual for asset X (a legitimate, non-compromised admin action).
4. Any other action that triggers `accrue()` on vault X within the same block (e.g., another user's deposit, another `system-borrow`) now computes `next-index()` over the entire frozen elapsed time (T0 to now), producing a materially higher real `index`/`lindex`, and this is written to the vault's `index`/`lindex` vars and `last-update`.
5. Because `market.clar`'s `index-cache` entry for `{timestamp: current-block-time, aid: X}` was already populated in step 2 with the pre-jump value, and cache lookups are `map-get?` hits (no re-accrual), every subsequent `borrow`/`repay`/liquidation call in the same block for asset X reads `get-cached-indexes` and unwraps the stale index via `unwrap-panic`, not the vault's true post-jump index: [5](#0-4) 

This is the direct analog of the reported bug class: a value (`index`/`lindex`) is bound to `market.clar`'s cache from a pass-through call that doesn't reflect true state, the state later moves (real accrual catches up once unpaused), and the stale cached value is reused for debt repayment/borrow accounting instead of being invalidated.

### Impact Explanation
`repay`'s `borrow-index` is taken straight from the stale cache and used to compute `max-repay-tokens`/`amount-to-repay`: [6](#0-5) 
If the cached index understates the true post-unpause index, users repaying (or having debt priced) in that same block settle their scaled debt at a lower token amount than actually owed, letting them extinguish debt for less than its true value — theft of unclaimed protocol/supplier yield (interest that should have accrued is never collected). If instead a zToken's `lindex` is involved in collateral valuation (`get-asset-value`) while stale, collateral can be mispriced, risking under-collateralized borrowing. This lands in the in-scope **High** impact ("theft of unclaimed yield") and can escalate toward **Critical** (insolvency) depending on the magnitude of the frozen period.

### Likelihood Explanation
This requires: (a) a vault's accrual to be paused and later unpaused within the same block, and (b) at least one accrual-triggering call to occur while paused, followed by another after unpausing, all in the same block. Pause/unpause is a normal DAO operational lever (not a compromise), and users can time their `borrow`/`repay` transactions around known pause-toggle windows, making this exploitable by any user monitoring the pause state, without needing DAO compromise.

### Recommendation
Invalidate or bypass `market.clar`'s `index-cache` entry whenever a vault's `accrue` pause state changes, or make `accrue-and-cache` always re-derive the index from the vault's authoritative state (ignoring pass-through/paused values) rather than caching a snapshot that can become stale within the same timestamp. Alternatively, key the cache invalidation on `last-update`/pause-state rather than solely on `stacks-block-time`.

### Proof of Concept
1. DAO calls `set-pause-accrue true` on `v0-vault-stx` (or any vault) at block time T.
2. User A calls `market.borrow` for STX; `accrue-and-cache` caches `{index: idx_frozen, lindex: lidx_frozen}` for `{timestamp: T, aid: STX}`.
3. Within the same block, DAO calls `set-pause-accrue false`.
4. User B triggers `system-borrow`/`system-repay` on `v0-vault-stx` directly, causing `accrue()` to run the "NOT PAUSED" branch and jump `index`/`lindex` forward to reflect the entire frozen elapsed period.
5. User A (or anyone) calls `market.repay` for STX in the same block; `get-cached-indexes` returns the stale `idx_frozen` from step 2 via `unwrap-panic`, computing `amount-to-repay` against the outdated (lower) index instead of the vault's true jumped index, letting debt be settled for less than actually owed.

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L841-844)
```text
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L853-865)
```text
                (var-set lindex nliq)
                false)
            (if (> treasury-lp u0)
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))

(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L245-257)
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1238-1291)
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

    (try! (vault-system-borrow asset-id amount funds-receiver))
    (let ((scaled-debt-added (convert-to-scaled-debt asset-id amount true))
          (borrow-index (get index (unwrap-panic (get-cached-indexes asset-id)))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1316-1346)
```text
(define-public (repay (ft <ft-trait>) (amount uint) (on-behalf-of (optional principal)))
  (let ((address (contract-of ft))
        (asset (try! (get-asset address)))
        (asset-id (get id asset))
        ;; defaults to payer (contract-caller) if not specified
        (account (match on-behalf-of behalf behalf contract-caller))
        
        ;; Step 1: Get position WITHOUT resolving prices
        (position (try! (get-position account)))
        (mask (get mask position))
        
        ;; Step 2: Accrue user's positions (populates cache for ztokens)
        (u-debt (accrue-user-debts (get debt position)))
        
        (borrow-index (get index (unwrap-panic (get-cached-indexes asset-id))))
        
        ;; Step 3: Get account debt FIRST to enable safe amount capping
        (account-scaled-debt (get-account-scaled-debt account asset-id))
        
        ;; Step 4: Calculate max repayable amount (actual debt in token), mul-div-up for safe upper bound
        (max-repay-tokens (mul-div-up account-scaled-debt borrow-index INDEX-PRECISION))
        
        ;; Step 5: Cap input amount at actual debt - prevents overflow in scaled calculation
        (safe-amount (min amount max-repay-tokens))
        
        ;; Step 6: Convert to scaled debt (amount is bounded)
        (scaled-debt-repayment (mul-div-down safe-amount INDEX-PRECISION borrow-index))

        (repaid-scaled-debt (min account-scaled-debt scaled-debt-repayment))
        (amount-to-repay (mul-div-up repaid-scaled-debt borrow-index INDEX-PRECISION))
        
```
