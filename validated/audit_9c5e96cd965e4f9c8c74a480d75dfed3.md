### Title
Stale per-timestamp index cache in `market.clar` can be read after a vault's liquidity index has moved within the same transaction - ([File: local-testing/contracts/market/market.clar])

### Summary
`market.clar` caches each vault's accrued `{index, lindex}` in the `index-cache` map, keyed by `{ timestamp: stacks-block-time, aid }`, to avoid repeated cross-contract accrue calls within the same block/transaction. This is the value-caching pattern flagged as analogous to the `computePoolAddress()` bug class: a value is bound once and later reused without being invalidated when its underlying source (the vault's actual on-chain index) is mutated again in the same transaction.

### Finding Description
`accrue-and-cache` reads `index-cache` keyed only on `stacks-block-time` and `aid`, and if a cache entry exists it is returned verbatim without re-querying the vault: [1](#0-0) 

The cache is written the first time a given `aid` is accrued in a transaction (cache miss → `vault-accrue` → `map-set index-cache`), and every subsequent call for that `aid` within the same `stacks-block-time` is served from this map instead of calling the vault again: [2](#0-1) 

`resolve-ztoken`, used for pricing zToken collateral, reads this cache directly via `get-cached-indexes` rather than calling the vault: [3](#0-2) 

However, the vault's actual `lindex`/`index` state var can be mutated by market-initiated vault calls that are *not* routed through `accrue-and-cache` at all — e.g. `vault-system-borrow`, `vault-system-repay`, and `vault-socialize-debt` each invoke vault entry points directly: [4](#0-3) 

Vault-level operations such as `socialize-debt` mutate `lindex` (the liquidity index) directly via `var-set lindex new-lindex`, independent of the time-based accrual formula: [5](#0-4) 

Because `index-cache` is keyed only by `{timestamp, aid}` and not invalidated when the vault's `lindex` is changed by one of these direct calls, a single market transaction that (1) first triggers `accrue-and-cache` for an asset (e.g. during a health check / oracle price resolution over a user's ztoken collateral), caching the pre-mutation index, and then (2) invokes an operation that mutates that same vault's `lindex` through a path bypassing the cache (e.g. `vault-socialize-debt`), and then (3) performs a second price resolution or accrual read for the same `aid` at the same `stacks-block-time`, will read the stale, pre-mutation `lindex`/`index` from `index-cache` instead of the vault's now-current value.

### Impact Explanation
If a multi-step market entry point (one that touches multiple assets/positions, e.g. a liquidation flow that can call `vault-socialize-debt` and also price zToken collateral for other users in the same call) exercises this ordering, health/LTV checks and zToken pricing for the remainder of the transaction will use a stale liquidity index rather than the just-updated one. Since the liquidity index directly scales collateral value (`resolve-ztoken` multiplies price by `cached-lindex`), an inconsistent (stale) index used for a health check can misprice collateral within that same transaction, temporarily distorting the solvency check used to gate borrow/withdraw/liquidate actions. This falls under temporary freezing/incorrect accounting of unclaimed yield-bearing collateral value during that transaction window (High-severity class per the freezing-of-funds impact bucket), though the actual bounded severity depends on how large the index delta can be within one block (accrual is otherwise time-gated, so the drift is limited to the effect of the direct `lindex` mutation itself, e.g. via `socialize-debt`).

### Likelihood Explanation
Likelihood is low-to-medium: it requires a single transaction that both reads (caches) a vault's index and then separately mutates that same vault's `lindex` via a code path that does not go through `accrue-and-cache` (e.g. `socialize-debt`), followed by a further cached read. This requires a specific multi-step entry point (e.g. a bad-debt socialization or liquidation flow that also touches ztoken pricing for the same asset) to exist and be reachable in one call; it is not a two-user race, matching the single-transaction analog class requested.

### Recommendation
Invalidate (delete or overwrite) the relevant `index-cache` entry whenever a vault-mutating call that changes `lindex`/`index` outside of `accrue-and-cache` is made (e.g. after `vault-socialize-debt`, `vault-system-borrow`, `vault-system-repay`), or route all such calls through `accrue-and-cache`/refresh the cache immediately after any direct vault state mutation so subsequent reads within the same transaction always reflect the latest on-chain index.

### Proof of Concept
Conceptual sequence within one market transaction, all at the same `stacks-block-time`:
1. Market performs a health check for a user holding zSTX collateral → `accrue-and-cache(STX)` cache miss → calls `vault-accrue`, caches `{index, lindex}` = V1 in `index-cache`. [1](#0-0) 
2. Same transaction subsequently calls `vault-socialize-debt(STX, amount)`, which directly `var-set`s the vault's `lindex` to a new value V2, bypassing `market.clar`'s cache entirely. [6](#0-5) [5](#0-4) 
3. Later in the same transaction, another price resolution for zSTX (`resolve-ztoken`) reads `get-cached-indexes` for `aid = STX`, which returns the stale V1 index from step 1 instead of the vault's actual current V2 index. [3](#0-2) 
4. Collateral valuation / health checks performed after step 2 use the stale index, producing a mismatch between the vault's real accounting state and the market's cached view for the remainder of the transaction.

### Citations

**File:** local-testing/contracts/market/market.clar (L115-118)
```text
;; -- Index cache (for accrual)
(define-map index-cache
  { timestamp: uint, aid: uint }
  { index: uint, lindex: uint })
```

**File:** local-testing/contracts/market/market.clar (L206-231)
```text
(define-private (vault-system-borrow (aid uint) (amount uint) (receiver principal))
  (if (is-eq aid STX) (contract-call? .vault-stx system-borrow amount receiver)
  (if (is-eq aid sBTC) (contract-call? .vault-sbtc system-borrow amount receiver)
  (if (is-eq aid stSTX) (contract-call? .vault-ststx system-borrow amount receiver)
  (if (is-eq aid USDC) (contract-call? .vault-usdc system-borrow amount receiver)
  (if (is-eq aid USDH) (contract-call? .vault-usdh system-borrow amount receiver)
  (if (is-eq aid stSTXbtc) (contract-call? .vault-ststxbtc system-borrow amount receiver)
  ERR-UNKNOWN-VAULT)))))))

(define-private (vault-system-repay (aid uint) (amount uint) (ft <ft-trait>) (ft-address principal))
  (if (is-eq aid STX) (contract-call? .vault-stx system-repay amount)
  (if (is-eq aid sBTC) (contract-call? .vault-sbtc system-repay amount)
  (if (is-eq aid stSTX) (contract-call? .vault-ststx system-repay amount)
  (if (is-eq aid USDC) (contract-call? .vault-usdc system-repay amount)
  (if (is-eq aid USDH) (contract-call? .vault-usdh system-repay amount)
  (if (is-eq aid stSTXbtc) (contract-call? .vault-ststxbtc system-repay amount)
  ERR-UNKNOWN-VAULT)))))))

(define-private (vault-socialize-debt (aid uint) (amount uint))
  (if (is-eq aid STX) (contract-call? .vault-stx socialize-debt amount)
  (if (is-eq aid sBTC) (contract-call? .vault-sbtc socialize-debt amount)
  (if (is-eq aid stSTX) (contract-call? .vault-ststx socialize-debt amount)
  (if (is-eq aid USDC) (contract-call? .vault-usdc socialize-debt amount)
  (if (is-eq aid USDH) (contract-call? .vault-usdh socialize-debt amount)
  (if (is-eq aid stSTXbtc) (contract-call? .vault-ststxbtc socialize-debt amount)
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

**File:** local-testing/contracts/vault/vault-stx.clar (L961-966)
```text
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

    (var-set lindex new-lindex)
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))
```
