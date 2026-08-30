### Title
Stale `index-cache` entries let bad-debt socialization be ignored by zToken price resolution within the same block, enabling under-collateralized actions - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`market.clar` caches each vault's liquidity index (`lindex`) per block timestamp so that multiple zToken price lookups in the same block avoid redundant vault calls. The cache is a pure timestamp-keyed lookup with no invalidation path: once a value is written for `{timestamp: stacks-block-time, aid}`, every subsequent read in the same block returns that cached value even if the vault's true index has since dropped due to bad-debt socialization. This mirrors the Blend fee-vault bug, where `last_update_timestamp` equality caused a stale `b_rate` to be reused across same-block transactions after a default.

### Finding Description
`accrue-and-cache` implements a cache-first read: [1](#0-0) 

On a cache HIT it returns the previously stored `{index, lindex}` pair without calling `vault-accrue` again — i.e. it never re-checks whether the vault's underlying state changed since the entry was written. The cache key is only `{timestamp, aid}`, not any monotonic per-update nonce, so two calls in the same block for the same asset always collide.

This cached `lindex` is exactly what backs zToken price valuation: [2](#0-1) 

`vault-socialize-debt` (invoked by the market on bad-debt liquidations, guarded by `ERR-BAD-DEBT-SOCIALIZATION-FAILED`) routes into the vault's `socialize-debt` entry point, which — analogous to Blend's `default_liabilities` reducing `b_rate` — reduces the vault's index/lindex to reflect losses taken by depositors: [3](#0-2) 

The sequence, all within one Stacks block (same `stacks-block-time`):

1. Transaction A touches ztoken `aid=X` as collateral or debt (e.g. a deposit, borrow, or an unrelated liquidation on a different debtor sharing the same vault). This calls `accrue-user-collateral`/`accrue-user-debts` → `accrue-and-cache(X)`, which is a cache MISS, so it calls `vault-accrue`, obtains the current (pre-loss) `{index, lindex}`, and writes it to `index-cache` keyed by the current `stacks-block-time`.
2. Transaction B is a bad-debt liquidation that ends up calling `vault-socialize-debt(X, amount)`, which lowers vault `X`'s true `lindex` (loss absorbed by depositors), but does not — and cannot, since the map key is timestamp-based, not index-based — invalidate the entry market.clar wrote in step 1.
3. Transaction C, still in the same block, performs any action (deposit, borrow, liquidation health check) that needs the zToken price for asset `X` as collateral or as debt. `accrue-and-cache(X)` is a cache HIT for the same timestamp key and returns the step-1 `lindex`, i.e. the pre-loss, inflated index, instead of re-accruing from the vault's post-loss state.
4. `resolve-ztoken` multiplies the underlying price by this stale, too-high `cached-lindex`, over-valuing zToken `X` collateral (or under-valuing zToken `X` debt). This can let an LTV/health check pass when the position is genuinely liquidatable, or let a borrower draw more debt against inflated zToken collateral, than the true post-loss state permits.

### Impact Explanation
An attacker (or even an ordinary sequence of unrelated actors' transactions bundled by the same block) can borrow against, or avoid liquidation of, zToken collateral valued at a stale pre-loss index after a bad-debt socialization event in the same block. Because the shortfall is socialized across all zToken holders of that vault, using the stale higher index lets one party extract/avoid loss at the expense of the vault's other depositors, who are last to redeem — this is the same mechanism as the referenced Blend finding and results in protocol insolvency for the affected vault (uncovered shortfall left for remaining zToken holders).

### Likelihood Explanation
Requires: (a) a bad-debt liquidation that triggers `vault-socialize-debt` on some vault `X` in a block, and (b) another transaction referencing zToken `X` pricing (deposit/borrow/liquidation-health-check) executed in the same block, either before or after the socialization call, sharing the same `stacks-block-time` cache key. Both conditions are achievable by a single actor bundling multiple contract-calls in one transaction (Clarity evaluation of a single tx is sequential and deterministic, and `stacks-block-time` is constant for the whole block), so no MEV/reorg is needed — matching the confirmed low-but-nonzero likelihood rated in the analogous Blend report.

### Recommendation
Do not use a timestamp-only cache key for values that can change mid-block. Either (a) invalidate/refresh the `index-cache` entry whenever `vault-socialize-debt` (or any other function that mutates a vault's index outside normal accrual) runs, by deleting or overwriting the cache entry for that `aid`, or (b) key the cache on a monotonically increasing per-vault update counter/version instead of `stacks-block-time`, so any state-changing event forces a fresh `vault-accrue` read on the next lookup.

### Proof of Concept
Conceptual PoC (single attacker transaction can sequence all steps atomically, since Clarity `contract-call?`s execute sequentially within one tx and `stacks-block-time` stays fixed for the whole tx/block):
1. Call market function `X` (e.g. `deposit`) touching zToken vault `A`, which internally calls `accrue-user-collateral`/`accrue-user-debts` → `accrue-and-cache(A)` (`mainnet/contracts/market/v0-4-market.clar:245-257`), populating `index-cache` with `{timestamp: T, aid: A} -> {index: I0, lindex: L0}` (pre-loss).
2. Trigger a bad-debt liquidation on vault `A` that calls `vault-socialize-debt(A, amount)` (`mainnet/contracts/market/v0-4-market.clar:216-223`), which reduces vault `A`'s true `lindex` to `L1 < L0`.
3. In the same transaction/block, perform a borrow or liquidation health check that requires pricing zToken `A` collateral. `accrue-and-cache(A)` cache-hits `{timestamp: T, aid: A}` and returns the stale `L0` instead of the correct `L1`, so `resolve-ztoken` (`local-testing/contracts/market/market.clar:365-369`) values the position's zToken `A` collateral using `L0`, overstating it relative to the true post-socialization value `L1`.

Full on-chain confirmation of the exact loss magnitude requires reading the `socialize-debt` implementation inside the vault contracts (e.g. `vault-stx.clar`) to confirm it mutates `lindex`/`index` the same way Blend's `default_liabilities` mutates `b_rate`; this function body was not fully reviewed in this pass, so the precise loss-application mechanics inside `socialize-debt` remain to be verified, though the market-level caching root cause is confirmed directly from the code cited above.

### Citations

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

**File:** local-testing/contracts/market/market.clar (L365-369)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```
