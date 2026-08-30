Found a valid analog in the liquidation path.

### Title
Stale `index-cache` reused after `vault-system-repay` mutates the borrow index within the same `liquidate` call - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`liquidate` populates `index-cache` for the debt asset once at the start (via `accrue-user-debts` → `accrue-and-cache`), then later calls `vault-system-repay`, which moves the vault's real borrow index by repaying `debt-to-repay`. Immediately afterward, the "other-debt-repayable" computation reuses `get-cached-indexes debt-aid` — the pre-repay cached value — instead of re-accruing, so the scaled-debt conversion for socialization is computed against a stale index.

### Finding Description
`accrue-and-cache` stores the vault's index/lindex keyed by `{ timestamp: stacks-block-time, aid }` and is intentionally reused for the rest of the block to save cross-contract calls: [1](#0-0) 

In `liquidate`, the cache is primed before any state-mutating call: [2](#0-1) 

The first read of `get-cached-indexes debt-aid` for `remaining-debt-to-repay` happens before `vault-system-repay` runs, which is fine: [3](#0-2) 

`vault-system-repay` then executes and mutates the vault's real borrow index/total-borrowed/assets state for `debt-aid`: [4](#0-3) 

Immediately after this state mutation, the code computes `other-debt-repayable` by calling `get-cached-indexes debt-aid` again — but this only reads the map, it does **not** re-invoke `accrue-and-cache`/`vault-accrue`, so it returns the same value cached before the repay: [5](#0-4) 

This is exactly the "cached value not invalidated when its source moves" pattern: the cache's invalidation key is `stacks-block-time`, not "has this vault's index changed within this transaction". Elsewhere in the same file, the developers are clearly aware that a cache must be refreshed after a vault mutation — `socialize-debt-asset` explicitly re-writes `index-cache` right after `vault-socialize-debt`: [6](#0-5) 

But no equivalent refresh follows `vault-system-repay` before `other-debt-repayable`/`no-collateral-left` is computed, and the subsequent `fold socialize-debt-asset` bad-debt socialization decision (`no-collateral-left`) depends on this stale `other-debt-repayable` value.

### Impact Explanation
`other-debt-repayable` feeds directly into `no-collateral-left`, which gates whether bad-debt socialization runs for the borrower's remaining debt. Using a pre-repay borrow index here understates or overstates how much of the "other" collateral can still cover debt, causing bad-debt socialization to be incorrectly skipped or incorrectly triggered on a still-viable position. Incorrectly skipping socialization when it was warranted leaves bad debt un-recognized in the vault's accounting (temporary freezing/misstatement of protocol funds, since `total-borrowed`/reserve accounting no longer matches actual recoverable debt), and incorrectly triggering socialization on a healthy remainder distributes losses to LPs that shouldn't be socialized. This lands in the "temporary freezing of funds" / accounting-insolvency-drift impact class.

### Likelihood Explanation
This triggers on every partial liquidation where the liquidated debt asset still leaves a debt/collateral remainder for the borrower (`coll-remaining > 0`), which is a normal, frequent liquidation pattern — no attacker-controlled interleaving is required beyond calling `liquidate` normally with a debt amount smaller than the borrower's full position. The interleaving (`vault-system-repay` mutating index state between two reads of the same cached index) is guaranteed by the function's own sequential Clarity evaluation order within the single transaction.

### Recommendation
After `vault-system-repay` mutates the debt asset's vault state, re-accrue and refresh `index-cache` for `debt-aid` (mirroring the pattern already used in `socialize-debt-asset`) before computing `other-debt-repayable`, or recompute the borrow index directly from `vault-accrue` instead of reading the pre-repay cached entry.

### Proof of Concept
1. Borrower has debt in `debt-aid` split conceptually into "target" collateral and "other" collateral, with `coll-remaining > 0` after seizing `coll-final-raw`.
2. `liquidate` primes `index-cache[debt-aid]` at line 1406 with the pre-repay borrow index.
3. `vault-system-repay` (line 1496) executes, repaying `debt-to-repay` and advancing the vault's real borrow index / `total-borrowed` for `debt-aid`.
4. At line 1522, `other-debt-repayable` is computed using `get-cached-indexes debt-aid`, which still returns the pre-repay index because `map-get?` does not re-trigger `vault-accrue`.
5. `other-borrow-idx` used to scale `other-tokens` into `other-scaled` is therefore stale, producing an incorrect `other-debt-repayable`, which incorrectly flips `no-collateral-left` and the downstream bad-debt socialization decision at line 1544-1559.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L890-895)
```text
            ;; Socialize in vault - pass scaled directly to avoid rounding
            (unwrap! (vault-socialize-debt asset-id scaled-debt) failed-status)
            ;; Refresh cache with new indexes post-write-down (lindex decreased)
            (map-set index-cache
                     { timestamp: stacks-block-time, aid: asset-id }
                     (unwrap! (vault-accrue asset-id) failed-status))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1405-1413)
```text
    ;; accrue FIRST - populates cache for zToken price resolution
    (u-debt (accrue-user-debts (get debt pos-full)))
    (u-coll (accrue-user-collateral (get collateral pos-full)))

    ;; NOW safe to resolve prices (cache is populated)
    (assets (get-assets mask))
    (notional-valued-assets (get-notional-evaluation { position: position, assets: assets }))
    (total-collateral-usd (get collateral notional-valued-assets))
    (total-debt-usd (get debt notional-valued-assets))
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1496-1496)
```text
    (try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1518-1525)
```text
          (other-debt-repayable
            (if (> other-coll-usd u0)
              (let ((other-adj (div-bps-down other-coll-usd (+ BPS liq-penalty-max)))
                    (other-tokens (mul-div-down other-adj (pow u10 debt-decimals) debt-price))
                    (other-borrow-idx (get index (unwrap-panic (get-cached-indexes debt-aid))))
                    (other-scaled (mul-div-down other-tokens INDEX-PRECISION other-borrow-idx)))
                (mul-div-up other-scaled other-borrow-idx INDEX-PRECISION))
              u0))
```
