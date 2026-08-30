Found it: `socialize-debt` directly mutates the vault's `lindex` without going through `accrue-and-cache`, so market.clar's per-block index cache can serve a stale `lindex` after a socialize-debt call within the same transaction.

### Title
Stale index-cache serves pre-socialize-debt liquidity index after vault write-down within the same transaction - (File: mainnet/contracts/vault/v0-vault-stx.clar, mainnet/contracts/market/v0-4-market.clar)

### Summary
`market.clar` caches each vault's `{index, lindex}` per `stacks-block-time` in `index-cache` via `accrue-and-cache`, treating a cache hit as authoritative for the rest of the transaction/block [1](#0-0) . However, `socialize-debt` in the vault directly writes a new `lindex` to vault storage without updating or invalidating market's `index-cache` [2](#0-1) . If a code path in market.clar primes the cache for an asset (e.g. via `accrue-and-cache`) and later in the same transaction triggers `vault-socialize-debt` (which mutates `lindex` at the vault), any subsequent `accrue-and-cache`/`get-cached-indexes` read for that same `aid` and timestamp still returns the old cached `lindex`, not the vault's updated post-write-down value.

### Finding Description
`accrue-and-cache` is a memoizing wrapper: on cache miss it calls `vault-accrue` and stores the returned `{index, lindex}` keyed by `(stacks-block-time, aid)`; on cache hit it returns the stored value without re-reading the vault [1](#0-0) . `resolve-ztoken`, used for pricing ztoken collateral, reads exclusively from this cache via `get-cached-indexes` [3](#0-2) .

Separately, `socialize-debt` on the vault (called by market via `vault-socialize-debt`) is a distinct entry point that bypasses `accrue`/`accrue-and-cache` entirely: it reads `lindex`/`principal-scaled`/`assets` directly from vault storage and writes a new `lindex` to reflect the loss, without any interaction with market's cache map [4](#0-3) .

Sequence:
1. Within a single transaction/block, market.clar calls `accrue-and-cache aid`, which is a cache MISS, so it calls `vault-accrue`, obtains the current `{index, lindex}`, and stores it in `index-cache` keyed by `(stacks-block-time, aid)` — this is the value bound.
2. Later in the same transaction, the market invokes a code path that leads to `vault-socialize-debt aid amount` (e.g. as part of liquidation/bad-debt handling), which calls the vault's `socialize-debt`, mutating `lindex` in vault storage to a lower value — this is the event that invalidates the previously bound value (the source moved).
3. Still in the same transaction, another operation (e.g. pricing ztoken collateral for a health check via `resolve-ztoken`, or another `accrue-and-cache` call for the same `aid`) hits the market's cache for `(stacks-block-time, aid)` — this is the later use — and receives the pre-socialize-debt `lindex` because the cache entry was never updated or invalidated by `socialize-debt`.
4. Under Clarity's left-to-right, sequential evaluation within a transaction, cross-contract state mutations performed by one contract call are immediately visible to subsequent calls in the same transaction, but the market's cache map is not re-read from the vault — it is a `map-get?`/`map-set` on market's own local map, so it is stale by construction until the next block (new `stacks-block-time`).

### Impact Explanation
Because `resolve-ztoken` prices ztoken collateral using the stale (higher) `lindex`, any downstream health check or notional evaluation in the same transaction that reads that ztoken's price will overvalue the underlying collateral relative to the post-write-down vault state. This can let a position that should be unhealthy after a bad-debt socialization pass a subsequent health check within the same transaction (e.g. permitting a borrow/collateral-add/removal, or blocking a liquidation that should otherwise succeed), leading to under-collateralized positions being created or preserved — a form of temporary/permanent freezing or mispricing of funds tied to the ztoken collateral valuation.

### Likelihood Explanation
This requires a single transaction where `socialize-debt` for a vault is triggered after that vault's index was already cached, and a later step in the same transaction reprices or re-evaluates that vault's ztoken. `socialize-debt` is restricted to authorized callers via `check-caller-auth` (market/liquidation contract) [5](#0-4) , so it depends on market.clar's own liquidation/bad-debt flow calling `vault-socialize-debt` mid-transaction and then re-consulting the cache — this is plausible in a composite liquidation flow that first accrues (caching), performs socialization, and then still needs to price remaining ztoken collateral for further checks in the same call.

### Recommendation
Invalidate or refresh the market's `index-cache` entry for an `aid` immediately after any call to `vault-socialize-debt` for that `aid` within the same transaction (e.g. `map-delete` the cache key, or re-fetch and `map-set` with the vault's post-socialization indexes) before any subsequent pricing or health-check logic consumes `get-cached-indexes`/`accrue-and-cache` for that asset.

### Proof of Concept
Conceptual PoC (Clarity, within one transaction handled by market.clar):
1. Call an operation that internally calls `(accrue-and-cache aid)` for vault `aid` — this is a cache miss, so `index-cache {timestamp: stacks-block-time, aid: aid}` is set to `{index: I0, lindex: L0}`.
2. Still in the same transaction, the same top-level call (e.g. a liquidation completion path) invokes `(vault-socialize-debt aid scaled-amount)`, which updates the vault's `lindex` to `L1 < L0` per `socialize-debt`'s write-down formula [6](#0-5) .
3. Still in the same transaction, the flow calls `(resolve-ztoken p aid)` (or another `accrue-and-cache aid` call) to price collateral for a remaining health check; this hits the cache and returns `L0` instead of `L1` [3](#0-2) .
4. The resulting collateral value used for the health check is inflated relative to the true post-socialization state, since `L0 > L1`.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L343-347)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L944-984)
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

    (print {
      action: "socialize-debt",
      caller: contract-caller,
      data: {
        scaled-amount: scaled-amount,
        debt-reduction: debt-reduction,
        principal-reduction: principal-reduction,
        old-lindex: current-lindex,
        new-lindex: new-lindex,
        old-total-assets: old-total-assets,
        principal-scaled: (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0),
        total-borrowed: (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0),
        index: idx
      }
    })

    (ok true)))
```
