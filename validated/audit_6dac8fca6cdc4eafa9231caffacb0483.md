### Title
Stale block-scoped liquidity-index cache lets zToken collateral be over-valued after same-block debt socialization - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`v0-4-market.clar` caches each vault's liquidity index in `index-cache`, keyed only by `{ timestamp: stacks-block-time, aid }`, and reuses that cached value for the rest of the block regardless of state-changing vault operations that occur afterward. If a vault's real liquidity index is written down mid-block (e.g. via bad-debt socialization) after the cache for that asset has already been populated, all subsequent zToken price resolutions in the same block keep using the pre-write-down (stale, too-high) index, letting collateral be over-valued for health checks.

### Finding Description
`accrue-and-cache` populates `index-cache` on a cache miss and simply returns the cached tuple on a hit: [1](#0-0) 

The cache key is only `{timestamp: stacks-block-time, aid}` - it has no dependency on the vault's actual index value or on any state-changing vault call. `resolve-ztoken`, which converts a zToken's underlying price into its zToken price by multiplying by the cached liquidity index, reads from this same cache: [2](#0-1) 

Separately, `vault-socialize-debt` calls straight through to the underlying vault contract's `socialize-debt`, which writes down the vault's real liquidity index (to absorb bad debt) without going through `accrue-and-cache`, so it never invalidates or updates `index-cache`: [3](#0-2) 

Collateral accrual for health checks is populated once via `accrue-user-collateral` / `accrue-and-cache`, and later price resolutions for the same asset in the same block are served from this cache instead of re-querying the vault: [4](#0-3) 

**Value bound:** the vault liquidity index for asset `aid`, cached in `index-cache` at `{timestamp: stacks-block-time, aid}` the first time it is needed in the block.
**Event that invalidates it:** any call to `vault-socialize-debt` (or any other vault mutation that changes the real liquidity index) for the same `aid` later in the same block.
**Later use:** any subsequent `price-resolve` → `resolve-ztoken` → `get-cached-indexes`/cache-hit for that `aid` within the same block, used to value zToken collateral for borrow/withdraw health checks.

### Impact Explanation
Because the cache is scoped to the block timestamp and not invalidated by vault mutations, a sequence of transactions within one block can leave zToken collateral valued at a higher, stale index after a debt-socialization event has actually reduced backing per share. A borrower can exploit this window to borrow against, or avoid triggering liquidation on, collateral that is really worth less than the cached price implies, creating under-collateralized debt and risking protocol insolvency (bad debt beyond what socialization already accounted for).

### Likelihood Explanation
Requires (1) a bad-debt socialization (or other index-writing vault mutation) to occur in a block, and (2) a borrow/withdraw transaction referencing the same zToken asset to land later in the same block, after the cache for that asset/timestamp was already warmed. Both conditions are plausible: socialization/liquidation events and normal user borrow/withdraw activity can coincide in the same block, especially since users or bots can intentionally sequence a transaction to land in the same block right after observing a socialization event.

### Recommendation
Invalidate or refresh `index-cache` for an asset whenever any operation (including `vault-socialize-debt` and other index-mutating vault calls) changes that vault's real index, e.g. by having those vault entry points call `accrue-and-cache`/re-store the fresh index, or by keying the cache to a monotonically increasing per-vault mutation counter rather than only `stacks-block-time`.

### Proof of Concept
1. Block N, Tx 1: A user action triggers `accrue-user-collateral` for zUSDC (aid `USDC`), causing `accrue-and-cache` to cache the current liquidity index `L0` at `{timestamp: T, aid: USDC}`.
2. Block N, Tx 2: A liquidation/bad-debt event calls `vault-socialize-debt` on `.v0-vault-usdc`, reducing the vault's real liquidity index to `L1 < L0`. `index-cache` is not touched.
3. Block N, Tx 3: A user borrows against zUSDC collateral. `price-resolve` → `resolve-ztoken` → cache lookup at `{timestamp: T, aid: USDC}` returns `L0` (stale) instead of `L1`, over-valuing the zUSDC collateral and permitting a loan that is not actually adequately collateralized against the post-socialization index.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L270-293)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L343-347)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```
