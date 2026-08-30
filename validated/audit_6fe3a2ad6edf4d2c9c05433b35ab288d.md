### Title
Timestamp-keyed `index-cache` in the market is not invalidated when `socialize-debt` mutates vault indexes out-of-band, causing stale index reuse within the same block - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
The market contract memoizes vault borrow/liquidity indexes in `index-cache` keyed only by `{ timestamp: stacks-block-time, aid }`, on the assumption that "no manual invalidation is needed" because a new block always produces a new timestamp key. That assumption breaks the moment a vault's index is mutated by a *different* code path than `vault-accrue` within the same block/timestamp - specifically `vault-socialize-debt`, which is routed directly to the vault and is never funneled through `accrue-and-cache`. Any cache entry for that `{timestamp, aid}` pair populated before the socialization call remains in the map afterward and will be returned verbatim by later cache reads in the same block, even though the vault's real index has moved.

### Finding Description
`index-cache` is defined as a per-timestamp memo of `{ index, lindex }` for each asset: [1](#0-0) 

`accrue-and-cache` is the only writer/reader gate for this cache: on a cache hit it returns the stored value with **no re-validation against the vault's current state**; on a miss it calls `vault-accrue` and stores the result: [2](#0-1) 

Debt valuation (`calculate-asset-notional-value`, used for health checks and liquidation pricing) and scaled-debt conversion both consume this cache directly, trusting it to reflect the true current index: [3](#0-2) [4](#0-3) 

However, `vault-socialize-debt` bypasses `accrue-and-cache` entirely and calls the vault's `socialize-debt` entry point directly: [5](#0-4) 

This is the same bug class as the reference finding: the reference `StRSR.setName` mutates the source (`name`) that the cached `_domainSeparatorV4` was derived from, but nothing invalidates the cached derivative, so later signature verification uses the stale value. Here, `socialize-debt` mutates the source (the vault's real index/lindex) that `index-cache` was derived from, but nothing invalidates the `{timestamp, aid}` entry already stored for the current block, so later debt/collateral valuation in the same block uses the stale cached index instead of the post-socialization one.

### Sequence
1. Within block/timestamp `T`, some operation on asset `aid` (e.g. `accrue-user-debts` during a borrow/repay/liquidate flow) calls `accrue-and-cache(aid)`, which misses, calls `vault-accrue`, and stores `{timestamp: T, aid}` -> `{index: I0, lindex: L0}` in `index-cache`.
2. Later in the same block `T` (a subsequent transaction, or a later step of a multi-step public function), `vault-socialize-debt(aid, amount)` is invoked, which directly mutates the vault's real `index`/`lindex` state to `I1`/`L1` (bad-debt socialization) without touching `index-cache`.
3. Any later call in block `T` that needs `aid`'s index - `calculate-asset-notional-value` (debt notional for health checks/liquidation) or `convert-to-scaled-debt` - invokes `accrue-and-cache`/`get-cached-indexes` again, hits the existing `{timestamp: T, aid}` entry, and returns the stale `{I0, L0}` instead of the true `{I1, L1}`.
4. Health checks, borrow limits, and liquidation seize/repay math for that block are computed against the wrong index, diverging from the vault's actual post-socialization accounting.

### Impact Explanation
Debt notional values computed from a stale (pre-socialization) index misstate a borrower's real debt for the remainder of the block. This can let an under-collateralized position pass a health check that should fail, or make a liquidation compute incorrect repay/seize amounts against a vault whose accounting was just changed - i.e., value can be extracted or preserved based on a value the protocol itself has already invalidated. This lands on temporary freezing/mispricing of funds and, in the liquidation-math case, theft of collateral/value that would otherwise be protected by an accurate health check, both of which are in-scope impact classes.

### Likelihood Explanation
`socialize-debt` is intended to run on a live vault with active borrowers, so a block containing both a socialization event and an ordinary borrow/repay/health-check/liquidation on the same asset is a realistic operational scenario, not a contrived edge case. The vulnerability requires no privileged access from the exploiting party - only that some socialization event lands in the same block/timestamp as other market activity, which the cache's timestamp-only key does nothing to prevent.

### Recommendation
Invalidate or update the relevant `index-cache` entry whenever `vault-socialize-debt` (or any other function that mutates a vault's index outside of `vault-accrue`) executes, e.g. by writing the vault's fresh post-mutation index/lindex into `index-cache` for the current `{timestamp, aid}` key immediately after the socialization call, or by removing the cache key so the next reader is forced to re-query the vault.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L112-115)
```text
;; -- Index cache (for accrual)
(define-map index-cache
  { timestamp: uint, aid: uint }
  { index: uint, lindex: uint })
```

**File:** mainnet/contracts/market/v0-4-market.clar (L216-223)
```text
(define-private (vault-socialize-debt (aid uint) (amount uint))
  (if (is-eq aid STX) (contract-call? .v0-vault-stx socialize-debt amount)
  (if (is-eq aid sBTC) (contract-call? .v0-vault-sbtc socialize-debt amount)
  (if (is-eq aid stSTX) (contract-call? .v0-vault-ststx socialize-debt amount)
  (if (is-eq aid USDC) (contract-call? .v0-vault-usdc socialize-debt amount)
  (if (is-eq aid USDH) (contract-call? .v0-vault-usdh socialize-debt amount)
  (if (is-eq aid stSTXbtc) (contract-call? .v0-vault-ststxbtc socialize-debt amount)
  ERR-UNKNOWN-VAULT)))))))
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

**File:** mainnet/contracts/market/v0-4-market.clar (L648-652)
```text
(define-private (convert-to-scaled-debt (asset-id uint) (amount uint) (round-up bool))
  (let ((borrow-index (get index (unwrap-panic (get-cached-indexes asset-id)))))
  (if round-up
    (mul-div-up amount INDEX-PRECISION borrow-index)
    (mul-div-down amount INDEX-PRECISION borrow-index))))
```
