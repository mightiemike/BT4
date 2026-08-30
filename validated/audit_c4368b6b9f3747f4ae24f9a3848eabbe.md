### Title
Stale Cached Vault Index Reused Across Batch Liquidations - (File: `market/market.clar` / `market/v0-4-market.clar`)

### Summary
`accrue-and-cache` populates a per-`(stacks-block-time, aid)` cache the first time a vault's liquidity/borrow index is needed in a transaction, and every later read for the same asset id in that same transaction returns the cached value with no invalidation check. `liquidate-multi` drives multiple independent `liquidate` calls (via `call-liquidate`) inside one atomic transaction, and `liquidate` itself socializes bad debt when a borrower's collateral cannot cover their debt. If liquidation of an earlier position in the batch triggers bad-debt socialization (or any other index-affecting vault mutation) for a given debt/collateral asset id, the mutation is written to the vault contract's own storage, but the market contract's `index-cache-` for that `(timestamp, aid)` key is never refreshed — so every subsequent liquidation in the same batch for that asset id prices debt/collateral using the pre-mutation index.

### Finding Description
`index-cache-` is declared as `{ timestamp: uint, aid: uint } -> { index: uint, lindex: uint }` and is only ever written once per timestamp/aid pair: [1](#0-0) [2](#0-1) 

The cached `lindex`/`index` is subsequently trusted by `resolve-ztoken` (price resolution) and directly by liquidation math (`rem-borrow-index`) with no re-accrual or freshness re-check against the vault's live state: [3](#0-2) [4](#0-3) [5](#0-4) 

`liquidate-multi` batches arbitrary borrower/asset combinations and executes them sequentially inside a single transaction via `map call-liquidate`: [6](#0-5) 

Within `liquidate`, bad debt is socialized when seized collateral cannot fully cover the capped debt (tracked via `bad-debt-socialized` in the emitted event), which requires a mutating call into the underlying vault (e.g. `vault-socialize-debt`) that changes that vault's real index/lindex outside of the timestamp-driven interest accrual model: [7](#0-6) [8](#0-7) 

Because `index-cache-` keys only on `(stacks-block-time, aid)` and never on a mutation counter or generation number, the first liquidation in a batch that touches asset id `A` populates the cache for `A` for the rest of the transaction. Any later liquidation in the same `liquidate-multi` batch that also involves asset `A` (as debt or collateral) will read the stale, pre-socialization index rather than the vault's updated state — this is exactly the "cached value not invalidated when its source moves" pattern: the bound value is the `{index, lindex}` cache entry for `(stacks-block-time, aid)`, the invalidating event is the vault-mutating `vault-socialize-debt`/vault index-write triggered by an earlier position's liquidation, and the later use is the next `call-liquidate` iteration pricing collateral/debt for the same asset id from the stale cache.

I could not retrieve the exact vault-side implementation of `vault-socialize-debt` (the vault contract files did not resolve via the paths queried), so the precise magnitude of the index shift caused by socialization is not independently confirmed from vault source in this pass — this should be verified directly in `market-vault`/vault contracts before treating this as fully proven.

### Impact Explanation
If a stale (higher-than-true) liquidity index is used to price collateral for a later liquidation in the same batch, the liquidator can seize collateral valued at a rate the vault no longer actually honors, effectively over-extracting value from the protocol/other users' pooled collateral relative to the post-socialization economic state — a form of direct fund extraction/protocol insolvency risk within a single transaction, landing on the Critical impact class (protocol insolvency / theft of funds at rest).

### Likelihood Explanation
Requires: (1) a borrower already deep enough underwater that liquidating them triggers bad-debt socialization, and (2) at least one other position in the same `liquidate-multi` batch sharing the same collateral or debt asset id. Both conditions are attacker-controllable — the caller chooses the batch composition and can engineer the debt/collateral overlap and ordering — but relies on the specific bad-debt-socialization code path being reached, so likelihood is moderate and contingent on further confirmation of what `vault-socialize-debt` actually mutates.

### Recommendation
Invalidate or re-derive the `index-cache-` entry for an asset id immediately after any vault mutation (socialize-debt, borrow, repay, deposit affecting index) rather than relying purely on `stacks-block-time` as the cache key; alternatively, force a fresh `vault-accrue` read (bypassing the cache) for the debt/collateral asset ids on every iteration inside `liquidate-multi`, or disallow overlapping asset ids for socialized positions within the same batch.

### Proof of Concept
1. Attacker (as liquidator) constructs a `liquidate-multi` call with two positions: Position 1 (borrower X) is deeply underwater on debt asset `USDC`, such that liquidating it triggers bad-debt socialization on the `USDC` vault, mutating its true liquidity index. Position 2 (borrower Y) also has `USDC` as debt or `zUSDC` as collateral.
2. `call-liquidate` processes Position 1 first: `liquidate` calls `accrue-and-cache USDC`, caching `{index, lindex}` for `(stacks-block-time, USDC)`; it then socializes bad debt, mutating the vault-usdc's real index via `vault-socialize-debt`.
3. `call-liquidate` processes Position 2 next in the same transaction/timestamp: any call to `accrue-and-cache USDC` or `get-cached-indexes USDC` returns the cache HIT from step 2, i.e., the pre-socialization index, not the vault's now-mutated real index.
4. Position 2's liquidation math (`process-debt-asset`/`process-collateral-asset`/`rem-borrow-index`) is computed against a value that no longer reflects the vault's true economic state, allowing collateral/debt to be mispriced relative to the post-socialization vault, in the liquidator's favor.

### Citations

**File:** local-testing/contracts/market/market.clar (L224-231)
```text
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

**File:** local-testing/contracts/market/market.clar (L1591-1608)
```text
            borrower: borrower,
            collateral-asset-id: coll-aid,
            collateral-asset-addr: coll-address,
            debt-asset-id: debt-aid,
            debt-asset-addr: debt-address,
            debt-repaid: debt-to-repay,
            debt-repaid-usd: debt-final-usd,
            collateral-seized: coll-final,
            collateral-price: coll-price,
            collateral-decimals: coll-decimals,
            liq-penalty-bps: liq-penalty,
            position-collateral-usd-before: total-collateral-usd,
            position-debt-usd-before: total-debt-usd,
            bad-debt-socialized: bad-debt-socialized
          }
        })
        
        (ok { debt: debt-to-repay, collateral: coll-final })))))
```

**File:** local-testing/contracts/market/market.clar (L1610-1622)
```text
;; Liquidates multiple positions atomically
;; Each position can have different: borrower, collateral asset, debt asset, and debt amount
;; Prevents front-running attacks that prevent bad debt socialization
;; Note: price-feeds not supported in batch - update prices separately or use individual liquidate()
;; Returns list of responses - one per position (ok/err)
;; Failed liquidations return error codes but don't revert entire batch
(define-public (liquidate-multi
                (positions (list 64 { borrower: principal,
                                      collateral-ft: <ft-trait>,
                                      debt-ft: <ft-trait>,
                                      debt-amount: uint,
                                      min-collateral-expected: uint })))
  (ok (map call-liquidate positions)))
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1477-1486)
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
