Found it. In `liquidate`, the debt vault's borrow index (`index`) and the collateral vault's liquidity index (`lindex`) are read once into `index-cache` (or `get-cached-indexes`) at the top of the function, then `vault-system-repay` is executed at line 1496, which mutates the debt vault's internal `index`/`principal-scaled` state (and, for the debt vault contract's own `system-repay`, calls its own `accrue` again). After that mutating call, the function at lines 1482 and 1522 (`rem-borrow-index` / `other-borrow-idx`) still reads `(get-cached-indexes debt-aid)` — the **stale, pre-repay cached index** — to compute `rem-scaled` / `other-scaled` amounts used for further debt/collateral bookkeeping, instead of re-querying the vault or refreshing the cache the way `socialize-debt-asset` explicitly does ("Refresh cache with new indexes post-write-down").

### Title
Liquidate uses stale cached borrow index after `vault-system-repay` mutates the debt vault's index - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`liquidate` populates `index-cache` for the debt asset before executing the repay, then calls `vault-system-repay` which changes the vault's on-chain index/principal state, but subsequently re-reads the now-stale `get-cached-indexes` value instead of refreshing it, using it to scale further debt/collateral math in the same transaction.

### Finding Description
At the start of `liquidate`, `accrue-user-debts`/`accrue-user-collateral` populate `index-cache` (keyed by `stacks-block-time`+`aid`) via `accrue-and-cache`. [1](#0-0)  Later, `vault-system-repay` is invoked to actually repay debt to the vault, which mutates the vault's `principal-scaled`/`index` state on-chain. [2](#0-1)  Despite this mutation, subsequent computations (`rem-borrow-index`, `other-borrow-idx`) read `(get-cached-indexes debt-aid)` again for the SAME timestamp key — since the cache map is keyed only by `{timestamp, aid}` and was already written earlier in this call, the entry is not refreshed, so these reads return the pre-repay index. [3](#0-2) [4](#0-3)  This is the same class of bug as WJLP: a mutating action against an external/underlying state source (vault repay) is not reflected in the cached accounting value used afterward. The developers were clearly aware of this exact risk, since `socialize-debt-asset` explicitly refreshes `index-cache` after `vault-socialize-debt` mutates the lindex ("Refresh cache with new indexes post-write-down (lindex decreased)"), [5](#0-4)  but the equivalent refresh is missing after `vault-system-repay` in `liquidate`.

### Impact Explanation
The stale index is used to compute `rem-scaled`/`other-scaled` values, which feed into how much *additional* debt is deemed "repayable" against the remaining/other collateral within the same liquidation call. Because the vault's index changed due to the repay just executed, the accounting derived from the stale index misstates the true scaled-debt-to-token conversion, causing the wrong amount of unclaimed/accrued interest yield to be socialized or attributed across users in this liquidation flow. This lands in the temporary/permanent freezing or misallocation of unclaimed yield among suppliers/borrowers tied to that vault.

### Likelihood Explanation
This triggers on every liquidation that reaches the `coll-remaining > 0` or "other collateral" branch — a fairly common liquidation path when a borrower has multiple collateral assets or the seized collateral doesn't fully cover the debt — requiring no special attacker capital, just a liquidator calling the standard `liquidate` entrypoint.

### Recommendation
After calling `vault-system-repay`, refresh `index-cache` for `debt-aid` with the vault's fresh index/lindex (mirroring the pattern already used in `socialize-debt-asset`) before using `get-cached-indexes` again for `rem-borrow-index`/`other-borrow-idx`.

### Proof of Concept
1. Borrower has debt in asset A and multiple collateral assets, with `coll-remaining > 0` after primary collateral seizure.
2. Liquidator calls `liquidate`; `accrue-user-debts` caches asset A's `index` at `stacks-block-time`. [6](#0-5) 
3. `vault-system-repay` executes, mutating vault A's principal/index. [2](#0-1) 
4. `rem-borrow-index`/`other-borrow-idx` re-read `get-cached-indexes debt-aid` — still the same-timestamp cached entry from step 2, not reflecting the repay. [7](#0-6) [8](#0-7) 
5. Debt-scaling for the "other collateral" bad-debt repay amount is computed off the pre-repay index, misallocating scaled debt/collateral accounting for that liquidation.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L891-895)
```text
            (unwrap! (vault-socialize-debt asset-id scaled-debt) failed-status)
            ;; Refresh cache with new indexes post-write-down (lindex decreased)
            (map-set index-cache
                     { timestamp: stacks-block-time, aid: asset-id }
                     (unwrap! (vault-accrue asset-id) failed-status))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1405-1407)
```text
    ;; accrue FIRST - populates cache for zToken price resolution
    (u-debt (accrue-user-debts (get debt pos-full)))
    (u-coll (accrue-user-collateral (get collateral pos-full)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1477-1485)
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
