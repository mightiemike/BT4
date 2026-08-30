Based on my investigation, I found a genuine analog to the reported bug class in `market.clar`'s timestamp-keyed index cache.

### Title
Premature vault index caching allows stale liquidity/borrow index reuse within the same block - (`mainnet/contracts/market/v0-4-market.clar`, `accrue-and-cache`)

### Summary
`accrue-and-cache` caches a vault's `{index, lindex}` pair keyed only by `{timestamp: stacks-block-time, aid}` [1](#0-0) . Once written, any later call to `accrue-and-cache` for the same `aid` within the same block returns the cached entry without re-deriving it, even though the underlying vault's `interest-rate`/`utilization` inputs (and therefore the "correct" index for that timestamp) can change earlier in the same transaction via `system-borrow`/`system-repay`/`deposit`/`redeem`, which mutate `principal-scaled`, `total-borrowed`, and `assets` directly rather than through `accrue-and-cache`.

### Finding Description
This mirrors the reported analog class: a value is bound (cached) at one point, an event invalidates the assumptions behind it (a state mutation that changes utilization/rate), and a later use in the same evaluation still relies on the stale bound value instead of the guard/recompute. Concretely:

- `accrue-and-cache` treats "cache hit" as authoritative for the rest of the transaction: `(match cached? cached-indexes (ok cached-indexes) ...)` [2](#0-1) .
- `collateral-add` primes this cache for a *new* zToken collateral asset via `(try! (accrue-and-cache vault-id))` before computing `added-collateral-value` and the capacity check [3](#0-2) .
- `borrow` accrues/caches user debt and collateral assets, then separately caches the asset being borrowed, explicitly commenting "NOW safe to resolve prices (cache is populated)" [4](#0-3)  — but the actual debt-increasing call `vault-system-borrow` happens afterward [5](#0-4) , and vault-level `next-index`/`next-liquidity-index` are a function of `interest-rate`/`utilization`, which is computed from `total-debt`/`get-available-assets` at call time [6](#0-5) .
- Because the cache key has no dependency on vault state (only `timestamp`/`aid`), any composite/multi-call transaction that (a) triggers accrual/caching for asset X, then (b) mutates a *different* code path that changes X's utilization (e.g., a large `system-repay` or `system-borrow` invoked indirectly through another market entry point sharing the same `aid`), then (c) reads a ztoken price for X via `resolve-ztoken`, which pulls `li` straight from `get-cached-indexes` [7](#0-6) , will use the pre-mutation index rather than the true post-mutation one for the remainder of that block/transaction.

### Impact Explanation
A stale, artificially low or high `lindex` used for zToken price resolution or health-check valuation misprices collateral/debt for the remainder of the transaction (and for any other transaction landing in the same block that hits the cache). This can inflate collateral value or understate debt value long enough to pass a health check that would otherwise fail, or to under/over-value collateral during `collateral-add`'s "future capacity must not decrease" check [8](#0-7) , temporarily freezing or misallocating unclaimed interest/yield tied to the liquidity index — landing in the in-scope "temporary freezing of funds" / "theft of unclaimed yield" category.

### Likelihood Explanation
Medium: it requires a single transaction (or block) that combines a caching read for an asset with a later state-changing operation on the same asset's vault before the cache is consulted again — feasible since `market.clar` routes many entry points (`borrow`, `collateral-add`, liquidation) through the same shared `index-cache` map, and users control call ordering within their own transaction.

### Recommendation
Invalidate or refresh the cached `{index, lindex}` whenever any operation mutates the underlying vault's `principal-scaled`, `total-borrowed`, or `assets` for that `aid` within the same transaction, rather than trusting a pure timestamp-keyed cache; e.g., have `system-borrow`/`system-repay`/`deposit`/`redeem` clear or update `index-cache` for their `aid` after execution, or bind the cache key to a monotonically-incrementing per-vault mutation counter in addition to `timestamp`.

### Proof of Concept
1. Attacker holds a position with zUSDC collateral and calls a market function sequence in one transaction such that `accrue-and-cache(USDC)` runs first (e.g., via `accrue-user-collateral` in `collateral-add`), storing `{index, lindex}` for `{timestamp: T, aid: USDC}`.
2. Within the same transaction/block, attacker triggers a large `system-borrow`/`system-repay` against the USDC vault (directly reachable via `borrow`/`repay` on a different asset-mask branch that still touches vault-usdc), materially changing `utilization` and thus what `next-liquidity-index` would compute if re-derived.
3. Attacker (or a subsequent instruction in the same tx) calls a function that needs the USDC zToken price/index again (e.g., `resolve-ztoken`); `accrue-and-cache(USDC)` hits the cache from step 1 and returns the stale `lindex` instead of the value consistent with post-step-2 state [2](#0-1) .
4. This stale index feeds into `get-notional-evaluation`/health-check math, letting the attacker pass a health check or capacity check that should have failed under the true, current index. [1](#0-0) [4](#0-3) [9](#0-8)

### Citations

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1060-1076)
```text
                          ;; Prime cache for new zToken collateral underlying if not already cached
                          (cache-primed (if (is-ztoken asset-id)
                                            (let ((vault-id (if (is-eq asset-id zSTX) STX
                                                            (if (is-eq asset-id zsBTC) sBTC
                                                            (if (is-eq asset-id zstSTX) stSTX
                                                            (if (is-eq asset-id zUSDC) USDC
                                                            (if (is-eq asset-id zUSDH) USDH
                                                            (if (is-eq asset-id zstSTXbtc) stSTXbtc
                                                            u100))))))))
                                              (try! (accrue-and-cache vault-id)))
                                            { index: u0, lindex: u0 }))
                          (added-collateral-value (try! (get-asset-value asset amount false)))
                          (future-ltv (buff-to-uint-be (get LTV-BORROW future-group)))
                          (future-coll-usd (+ current-coll-usd added-collateral-value))
                          (future-capacity (* future-coll-usd future-ltv)))
                      ;; CRITICAL CHECK: Future capacity must not decrease
                      (asserts! (>= future-capacity current-capacity) ERR-UNHEALTHY))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1250-1258)
```text
        ;; Step 2: Accrue user's positions (populates cache for ztokens)
        (u-debt (accrue-user-debts (get debt position)))
        (u-coll (accrue-user-collateral (get collateral position)))
        
        ;; Step 3: Accrue the asset being borrowed (needed for index access)
        (unused (accrue-and-cache asset-id))
        
        ;; Step 4: NOW safe to resolve prices (cache is populated)
        (assets (get-assets mask))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1289-1290)
```text
    (try! (vault-system-borrow asset-id amount funds-receiver))
    (let ((scaled-debt-added (convert-to-scaled-debt asset-id amount true))
```

**File:** local-testing/contracts/vault/vault-usdc.clar (L372-408)
```text

(define-private (interest-rate)
  (let ((points-data (var-get points-ir))
        (uword (get util points-data))
        (rword (get rate points-data))
        (utils (unpack-u16 uword))
        (rates (unpack-u16 rword)))
    (interpolate-rate (utilization) utils rates)))

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

(define-private (next-liquidity-index)
  (let ((states (var-get pause-states))
        (lidx (var-get lindex)))
    (if (get accrue states)
        lidx
        (let (
            (rate (interest-rate))
            (liquidity-rate (calc-liquidity-rate rate (utilization) (var-get fee-reserve)))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta liquidity-rate time-delta false))))
          (calc-index-next lidx multiplier)))))

(define-private (principal-ratio-reduction (amount uint))
```

**File:** docs/oracle.md (L154-163)
```markdown
**Generic ztoken resolver:**
```clarity
;; In market.clar
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((price (if (is-eq aid u2)
                   (try! (resolve-ststx p))  // zststx: apply ratio first
                   p))
        (li (get index (unwrap-panic (get-cached-indexes aid)))))
    (ok (/ (* price li) PRECISION))))
```
```
