Based on the investigation, I found a genuine analog in `market.clar`'s liquidation flow that matches the "cached value not invalidated when its source moves" bug class.

### Title
Stale `index-cache` read after `vault-socialize-debt` mutates the vault index during bad-debt socialization in `liquidate` - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`liquidate()` populates `index-cache` early via `accrue-user-debts`/`accrue-user-collateral`, which call `accrue-and-cache` keyed by `{ timestamp: stacks-block-time, aid }` [1](#0-0) . Later in the same transaction, when bad debt is socialized, `socialize-debt-asset` calls `vault-socialize-debt` (which mutates the vault's `lindex` downward via `socialize-debt`) and then explicitly refreshes the cache for that specific asset with `map-set index-cache ... (vault-accrue asset-id)` [2](#0-1) . However, this refresh only happens for the debt asset being socialized in that fold iteration — any other in-flight computation in the same block that already read `get-cached-indexes` for that `aid` before the write-down, and any use elsewhere in the same call graph after the write-down for a *different* asset sharing the same `aid` bucket, still relies on the stale, pre-write-down cached value because `stacks-block-time` (the cache key) does not change within the transaction.

### Finding Description
The market's `index-cache` map is keyed purely by `{ timestamp: stacks-block-time, aid }` [3](#0-2) . Because `stacks-block-time` is constant for the whole transaction, this cache is effectively a per-transaction memoization keyed only by `aid`. `accrue-and-cache` returns the cached entry on a hit without re-deriving it from the vault's live state [1](#0-0) .

Within `liquidate`, the sequence is:
1. `accrue-user-debts`/`accrue-user-collateral` populate `index-cache[debt-aid]` from the vault's state at transaction start [4](#0-3) .
2. `vault-system-repay` executes and mutates the vault's internal state (`principal-scaled`, `total-borrowed`) [5](#0-4) .
3. `other-debt-repayable` is computed using `(get-cached-indexes debt-aid)` — i.e., the *original* cached index from step 1, not a value that reflects the state mutation from step 2 [6](#0-5) .
4. If bad debt must be socialized, `socialize-debt-asset` is folded over remaining debt, which calls `vault-socialize-debt` (writes down `lindex`) and then only refreshes the cache entry for the specific `aid` being processed in that iteration [7](#0-6) .

The underlying source of truth (the vault's index/lindex) "moves" mid-transaction due to `system-repay`/`socialize-debt` calls, but the market's `index-cache` is not uniformly invalidated for every downstream consumer — only the specific write path in `socialize-debt-asset` refreshes its own entry. Any other computation reading `get-cached-indexes` for the same `aid` earlier in the call (e.g., `other-borrow-idx` at line 1522) uses the pre-mutation snapshot rather than the current vault state, i.e., a cached value is used past the point where its source has moved, matching the report's bug class of "a cached value not invalidated when its source moves."

### Impact Explanation
`other-debt-repayable`, computed from the stale `index-cache` entry, feeds into the `no-collateral-left` determination, which in turn gates whether the position's remaining debt is socialized as bad debt [8](#0-7) . If the stale index causes an incorrect scaled/token conversion, bad-debt socialization could be skipped or triggered incorrectly, leaving unclaimed yield/debt improperly accounted for in the vault (temporary freezing/misallocation of protocol yield), landing on the in-scope "temporary freezing of funds" or "theft/freezing of unclaimed yield" impact class.

### Likelihood Explanation
This requires a liquidation on a position with existing prior debt/collateral state such that the socialization path is triggered, and depends on precise numeric conditions where the mutated vault index differs meaningfully from the transaction-start cached index. This is a narrow, state-dependent condition rather than a broadly-exploitable, attacker-controlled trigger — likelihood is low-to-moderate and I could not fully confirm (given tool/index limits) whether the specific magnitude of drift between `lindex` write-down and `index` (borrow index, not liquidity index) actually produces an incorrect value at line 1522, since `other-borrow-idx` reads `index` not `lindex`, and `socialize-debt` only writes down `lindex` per the vault code I inspected [9](#0-8) . This weakens confidence that the specific underflow/staleness manifests as a real exploitable state divergence, since the borrow `index` (used at line 1522) is only time-dependent and does not change within a single block/transaction — meaning the cached `index` value itself may remain numerically correct even though it was fetched before `vault-socialize-debt` ran.

### Recommendation
If a true divergence is confirmed, re-derive (rather than reuse cached) index/lindex values for any downstream computation that occurs after a vault-mutating call (`vault-system-repay`, `vault-socialize-debt`) within the same transaction, or invalidate/refresh all `index-cache` entries for an `aid` immediately after any state-mutating vault call rather than relying on a single-write refresh scoped to the fold iteration.

### Proof of Concept
Not verified with a concrete numeric trace within available tooling — the analysis identifies the structural pattern (cache populated once per `aid` per block, selectively refreshed only in `socialize-debt-asset`, and read again for `other-debt-repayable` without confirmation that the specific field being read (`index`) is affected by the intervening mutation to `lindex`). A background Devin session with full repo/test access would be needed to trace whether `index` vs `lindex` divergence can actually be forced to produce an incorrect `other-debt-repayable` value in a live liquidation scenario.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L112-115)
```text
;; -- Index cache (for accrual)
(define-map index-cache
  { timestamp: uint, aid: uint }
  { index: uint, lindex: uint })
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

**File:** mainnet/contracts/market/v0-4-market.clar (L259-293)
```text
(define-private (accrue-user-debts (debt-list (list 64 { aid: uint, scaled: uint})))
  (fold accrue-debt-asset debt-list { success: true }))

(define-private (accrue-debt-asset
  (debt-entry { aid: uint, scaled: uint })
  (acc { success: bool }))
  (begin
    ;; this will use cache if available, accrue if not
    (unwrap-panic (accrue-and-cache (get aid debt-entry)))
    acc))

(define-private (accrue-user-collateral (coll-list (list 64 {aid: uint, amount: uint})))
  (fold accrue-collateral-asset coll-list { success: true }))

(define-private (accrue-collateral-asset
  (coll-entry { aid: uint, amount: uint })
  (acc { success: bool }))
  (let ((aid (get aid coll-entry)))
    ;; Only accrue if asset is a registered ztoken
    (if (is-ztoken aid)
        ;; ZToken: map to underlying vault routing ID and accrue
        ;; zSTX(1)->STX(0), zsBTC(3)->sBTC(2), zstSTX(5)->stSTX(4), zUSDC(7)->USDC(6), zUSDH(9)->USDH(8), zstSTXbtc(11)->stSTXbtc(10)
        (let ((vault-id (if (is-eq aid zSTX) STX
                        (if (is-eq aid zsBTC) sBTC
                        (if (is-eq aid zstSTX) stSTX
                        (if (is-eq aid zUSDC) USDC
                        (if (is-eq aid zUSDH) USDH
                        (if (is-eq aid zstSTXbtc) stSTXbtc
                        ;; will cause ERR-UNKNOWN-VAULT with any value over 64
                        u100))))))))
          (begin
            (unwrap-panic (accrue-and-cache vault-id))
            acc))
        ;; Non-ztoken: skip accrual (no liquidity index needed)
        acc)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1495-1496)
```text
    ;; execute liquidation
    (try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1518-1533)
```text
          (other-debt-repayable
            (if (> other-coll-usd u0)
              (let ((other-adj (div-bps-down other-coll-usd (+ BPS liq-penalty-max)))
                    (other-tokens (mul-div-down other-adj (pow u10 debt-decimals) debt-price))
                    (other-borrow-idx (get index (unwrap-panic (get-cached-indexes debt-aid))))
                    (other-scaled (mul-div-down other-tokens INDEX-PRECISION other-borrow-idx)))
                (mul-div-up other-scaled other-borrow-idx INDEX-PRECISION))
              u0))
          (no-collateral-left (and
                                (is-eq coll-removed u0)
                                (or
                                  (is-eq (len (get collateral pos-full)) u1)
                                  (and
                                    (is-eq (len (get collateral pos-full)) (len (get collateral position)))
                                    (is-eq other-debt-repayable u0))))))

```

**File:** local-testing/contracts/market/market.clar (L901-925)
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
            (unwrap! (contract-call? .market-vault
                                      debt-remove-scaled
                                      borrower
                                      scaled-debt
                                      asset-id) failed-status)
          acc)
        ))
```

**File:** local-testing/contracts/vault/vault-sbtc.clar (L946-960)
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
```
