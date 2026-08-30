Confirmed root cause: `socialize-debt` mutates `lindex` (and `index`-relevant state via `principal-scaled`/`total-borrowed`) directly, without going through `accrue`, and this write is not routed through the `index-cache` used by `market.clar`.

### Title
Stale per-timestamp `index-cache` in `market.clar` can serve outdated liquidity index after `socialize-debt` bypasses vault accrual - (File: `local-testing/contracts/market/market.clar`)

### Summary
`market.clar` caches each vault's `{index, lindex}` in `index-cache` keyed by `{timestamp: stacks-block-time, aid}` the first time `accrue-and-cache` is called for that asset in a block/transaction, and returns the cached tuple on every subsequent call within the same timestamp instead of re-querying the vault. `socialize-debt` in the vault contracts writes `lindex` (and debt state) directly, bypassing `accrue`. If `socialize-debt` runs for a vault after `market.clar` has already populated `index-cache` for that `aid` in the same transaction, later reads of that asset's `lindex` (e.g., for ztoken price resolution / collateral notional valuation) in the same call will use the stale, pre-socialization value instead of the corrected one.

### Finding Description
`accrue-and-cache` in `market.clar` implements a memoization pattern: [1](#0-0) 

The cache is keyed only by `{timestamp, aid}` — it does not consider whether the vault's underlying state (`index`/`lindex`) has been mutated by a call that occurred *after* the cache was populated within the same block. `get-cached-indexes` similarly just performs a `map-get?` on that key: [2](#0-1) 

Meanwhile, `socialize-debt` in each vault contract writes `lindex` directly to reflect a loss write-down, without calling `accrue` and without any hook back into `market.clar`'s cache: [3](#0-2) 

This is the exact bug class in the report: a value (`{index, lindex}`) is cached from its source (the vault), the source is then mutated by a different code path (`socialize-debt`) that does not invalidate the cache, and a later read within the same transaction/timestamp consumes the now-incorrect cached value (`get-notional-evaluation` → `calculate-asset-notional-value` uses `accrue-and-cache` for debt notional and, for ztoken collateral pricing, the oracle resolver reads `get-cached-indexes` to compute the ztoken price): [4](#0-3) [5](#0-4) 

Sequence:
1. Within a single transaction (e.g., a liquidation flow that both socializes bad debt on one vault and then evaluates/prices positions using that vault's ztoken), `market.clar` calls `accrue-and-cache(aid)` for a vault, populating `index-cache[{timestamp, aid}]` with the pre-socialization `{index, lindex}`.
2. Still within the same transaction, `socialize-debt` is invoked on that vault (via `vault-socialize-debt`), which directly overwrites `lindex` to a lower value reflecting the write-down of bad debt, without touching `index-cache`.
3. A subsequent step in the same transaction (still same `stacks-block-time`) that needs that vault's ztoken price or liquidity index — e.g., `resolve-ztoken`/`calculate-asset-notional-value` for a different position holding the same ztoken as collateral — calls `get-cached-indexes`/`accrue-and-cache` again for the same `aid`, and the cache HIT returns the stale, higher `lindex` instead of the corrected lower value.
4. Downstream collateral valuation for other positions holding that ztoken is computed with an inflated `lindex`, overstating collateral value for the remainder of that transaction.

### Impact Explanation
This can cause other users' collateral (any position holding the affected ztoken) to be valued higher than it actually is within the same transaction that the debt was socialized, potentially allowing a health check to pass when it should fail (e.g., permitting an under-collateralized borrow, or preventing/altering a liquidation outcome) in that same call. This falls under temporary freezing/mispricing risk and possible protocol insolvency exposure since collateral backing is misstated relative to freshly socialized losses — landing in the Critical/High impact bucket (protocol insolvency / theft-adjacent mispricing) depending on the exact call ordering exploited.

### Likelihood Explanation
Likelihood is moderate: it requires a specific interleaving where `socialize-debt` for a vault and a ztoken price/notional evaluation for the *same* vault's ztoken occur within the same transaction and the cache was already warmed for that `aid` before the socialization call. This is plausible in liquidation-related flows that combine debt socialization with position health re-evaluation, but it is not a routine user-triggered path — it likely requires a specific caller ordering, so it is not "easy" to trigger opportunistically without a supporting multi-step transaction.

### Recommendation
Invalidate (or refresh) the `index-cache` entry for an `aid` whenever `socialize-debt` (or any other vault function that directly mutates `index`/`lindex` outside of `accrue`) is invoked from `market.clar`. Concretely, after calling `vault-socialize-debt`, either `map-delete` the corresponding `index-cache` entry for that `aid`/timestamp or immediately re-populate it with the fresh `{index, lindex}` returned by the vault call, so subsequent reads in the same transaction are guaranteed fresh.

### Proof of Concept
Not independently executable from the indexed documentation alone — the exact call site where `market.clar` invokes `vault-socialize-debt` in the same transaction as a subsequent ztoken price/notional lookup for the same asset was not fully located within the indexing limits of this pass. A Devin session with full repository access should trace `market.clar`'s liquidation/`socialize-debt` entry point end-to-end to confirm the concrete call ordering that triggers the stale-cache read, and add a regression test asserting `get-cached-indexes` reflects post-`socialize-debt` state within a single transaction.

### Citations

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

**File:** local-testing/contracts/market/market.clar (L966-967)
```text
(define-read-only (get-cached-indexes (aid uint))
  (map-get? index-cache { timestamp: stacks-block-time, aid: aid }))
```

**File:** local-testing/contracts/vault/vault-ststxbtc.clar (L948-970)
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

**File:** mainnet/contracts/market/v0-4-market.clar (L563-569)
```text
        (debt-scaled   (find-debt-scaled debt-list asset-id))
        (debt-notional (if (> debt-scaled u0) ;; use cache instead here
                           (let ((cached (unwrap-panic (accrue-and-cache asset-id)))
                                 (ib (get index cached))
                                 (actual (mul-div-up debt-scaled ib INDEX-PRECISION)))
                             (normalize (* actual price) decimals true))
                           u0)))
```

**File:** docs/oracle.md (L157-162)
```markdown
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((price (if (is-eq aid u2)
                   (try! (resolve-ststx p))  // zststx: apply ratio first
                   p))
        (li (get index (unwrap-panic (get-cached-indexes aid)))))
    (ok (/ (* price li) PRECISION))))
```
