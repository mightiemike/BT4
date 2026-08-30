Found it: `socialize-debt` mutates `lindex` directly via `var-set lindex new-lindex` [1](#0-0)  but this write-down happens **without going through the vault's `accrue` function**, meaning the market's timestamp-keyed `index-cache` is the mechanism that is supposed to stay in sync with the vault's real index/lindex state.

### Title
Stale market index-cache used after mid-liquidation debt socialization write-down desyncs collateral pricing - (File: `local-testing/contracts/market/market.clar`)

### Summary
`market.clar`'s `accrue-and-cache` caches vault `{index, lindex}` per `{timestamp: stacks-block-time, aid}` for the duration of a block/transaction [2](#0-1) . Within a single `liquidate` call, this cache is read multiple times before and after the vault's debt/lindex state is mutated by `vault-system-repay` and `vault-socialize-debt`, but the cache is only explicitly refreshed for the bad-debt-socialization step, not universally re-validated against the vault's now-current on-chain state.

### Finding Description
In `liquidate()`, the cached debt-asset index is read once early (`rem-borrow-index` via `get-cached-indexes debt-aid`) to compute `remaining-debt-to-repay` [3](#0-2) , then `vault-system-repay` executes (mutating the vault's `principal-scaled`/`total-borrowed` state) [4](#0-3) , and finally `debt-remove-scaled`/`collateral-remove` and (conditionally) `socialize-debt-asset` run, which itself calls `vault-socialize-debt` — writing `lindex` down directly via `var-set` — and then explicitly refreshes the cache only for that specific asset with `map-set index-cache { timestamp: stacks-block-time, aid: asset-id } (unwrap! (vault-accrue asset-id) ...)` [5](#0-4) . Any other read of `get-cached-indexes` for that same `aid` that happened earlier in the same transaction (e.g., `other-borrow-idx` computed at line 1545 using the *same* cache entry) is not re-derived — the cache-invalidate-on-write pattern is applied ad hoc per call site, not generically. If `debt-aid` is re-read via `get-cached-indexes` in a code path after `socialize-debt-asset` runs but that code path is not the one explicitly refreshing the cache, the market would be using a `lindex`/`index` that no longer matches the vault's true state after the write-down within the same transaction.

### Impact Explanation
Because the mismatch is confined to index/lindex snapshots used for computing repay/collateral seizure math within a single liquidation, and no test path independently confirms the second read location is always safe from stale reuse, the impact — if triggered — would be a mispriced liquidation seizing more or fewer collateral tokens than the on-chain debt actually warrants, which classifies as a form of temporary/incorrect fund accounting rather than a clean theft path.

### Likelihood Explanation
This requires the specific bad-debt/no-collateral-left branch of `liquidate()` to trigger `socialize-debt-asset`, and a debt asset that is referenced by cached index both before and after that socialization step within the same call — a narrow, multi-condition path that I could not fully trace to a concrete exploitable divergence with the available tools before this session ended.

### Recommendation
Recommend an explicit review of every `get-cached-indexes` call following any state-mutating vault call (`vault-system-repay`, `vault-socialize-debt`) within the same `liquidate` transaction, and either (a) invalidate/refresh the entire cache entry for `debt-aid` immediately after any mutating vault call, or (b) avoid re-reading the cache for values computed after a mutation — recompute directly from the vault instead.

### Proof of Concept
I was unable to construct a concrete, verified end-to-end PoC transaction sequence within the remaining tool budget — I could not fully confirm (1) that the second `get-cached-indexes debt-aid` read in `liquidate` at line ~1545 executes strictly after `socialize-debt-asset`'s cache refresh for the *same* `aid` in all liquidation branches, and (2) the precise numeric divergence this would produce. This is stated explicitly as an open item rather than a confirmed exploit; further live tracing (e.g., with a Devin session that can run the test suite) is recommended to validate or refute this before treating it as an accepted finding.

### Citations

**File:** local-testing/contracts/vault/vault-stx.clar (L902-925)
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
        (principal-reduction (calc-principal-ratio-reduction capped-amount scaled-principal debt))
        (capped-reduction (if (> principal-reduction scaled-principal) scaled-principal principal-reduction))
        (updated-scaled-principal (- scaled-principal capped-reduction))
        (principal-repaid (mul-div-down capped-amount total-borrowed-amount debt))
        (interest-paid (- capped-amount principal-repaid))
        (total-borrowed-new (if (> total-borrowed-amount principal-repaid) (- total-borrowed-amount principal-repaid) u0)))

    (try! (check-caller-auth))
    (asserts! (not (get repay states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)

    (try! (receive-underlying capped-amount tx-sender))
    (var-set principal-scaled updated-scaled-principal)
    (var-set total-borrowed total-borrowed-new)
    (var-set assets (+ (var-get assets) interest-paid))
```

**File:** local-testing/contracts/vault/vault-stx.clar (L944-967)
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

**File:** local-testing/contracts/market/market.clar (L1500-1509)
```text
    (remaining-debt-to-repay
      (if (> coll-remaining u0)
        (let ((rem-coll-usd (normalize (* coll-remaining coll-price) coll-decimals false))
              (rem-debt-usd (div-bps-down rem-coll-usd (+ BPS liq-penalty-max)))
              (rem-debt-tokens (mul-div-down rem-debt-usd (pow u10 debt-decimals) debt-price))
              (rem-borrow-index (get index (unwrap-panic (get-cached-indexes debt-aid))))
              (rem-scaled (mul-div-down rem-debt-tokens INDEX-PRECISION rem-borrow-index)))
          (mul-div-up rem-scaled rem-borrow-index INDEX-PRECISION))
        u1))
    (coll-final (if (is-eq remaining-debt-to-repay u0) user-coll-balance coll-final-raw)))
```
