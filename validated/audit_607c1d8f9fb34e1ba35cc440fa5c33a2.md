## Root Cause Confirmed

`resolve-ztoken` in `market.clar` prices zToken collateral using the `lindex` (liquidity index) pulled from `index-cache`:

```clarity
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
``` [1](#0-0) 

The cache is keyed only by `{ timestamp: stacks-block-time, aid }` and returns the cached value on any hit, never re-querying the vault once populated for that block:

```clarity
(define-private (accrue-and-cache (aid uint))
  (let ((cache-key { timestamp: stacks-block-time, aid: aid })
        (cached? (map-get? index-cache cache-key)))
    (match cached?
      cached-indexes (ok cached-indexes)          ;; cache HIT: return cached value (1 read only)
      (let ((indexes (try! (vault-accrue aid))))
        (map-set index-cache cache-key indexes)
        (ok indexes)))))
``` [2](#0-1) 

`lindex` is written down by the vault's `socialize-debt` function whenever bad debt is socialized — a write that can occur mid-transaction, inside `liquidate`/`liquidate-multi`:

```clarity
(var-set lindex new-lindex)
(var-set principal-scaled ...)
(var-set total-borrowed ...)
(var-set assets ...)
``` [3](#0-2) 

`liquidate` triggers this via `fold socialize-debt-asset` when a position has no collateral left after seizure: [4](#0-3) 

`liquidate-multi` maps `call-liquidate` over up to 64 positions in the same transaction/block:
```clarity
(define-public (liquidate-multi (positions (list 64 {...})))
  (ok (map call-liquidate positions)))
``` [5](#0-4) 

### Title
Stale liquidity-index cache leaves later same-block liquidations mispricing zToken collateral after bad-debt socialization - (File: `mainnet/contracts/market/v0-4-market.clar`, `mainnet/contracts/vault/v0-vault-*.clar`)

### Summary
The market's `index-cache` binds a vault's `{index, lindex}` pair for the entire block (keyed by `stacks-block-time`, not by a per-vault-write nonce). Within a single transaction (`liquidate-multi`, or `liquidate-redeem` chains), an earlier liquidation can trigger `socialize-debt` on a vault, which writes down `lindex` to reflect a loss. Because the cache was already primed for that `aid` earlier in the same transaction/block, all subsequent positions processed in the same call that hold zToken collateral of that vault continue to be valued with the pre-write-down (stale, higher) `lindex`, via `resolve-ztoken`.

### Finding Description
1. Transaction calls `liquidate-multi` with N positions, several holding the same zToken collateral (e.g., `zUSDC`, backed by `vault-usdc`).
2. Position 1 is processed: `accrue-and-cache USDC` caches `{index, lindex}` for `stacks-block-time` in `index-cache` [2](#0-1) . Its collateral is fully seized and debt cannot be fully repaid, so `liquidate` calls `fold socialize-debt-asset` → `contract-call? .vault-usdc socialize-debt`, which reduces `lindex` on `vault-usdc` to reflect the just-realized loss [6](#0-5) .
3. Position 2 (same transaction, still same `stacks-block-time`) holds `zUSDC` collateral. `liquidate` calls `resolve-ztoken` → `get-cached-indexes USDC`, which hits the cache and returns the **old, higher** `lindex` from step 1, because `accrue-and-cache` never re-queries the vault once a cache entry exists for that block [1](#0-0) .
4. Position 2's zUSDC collateral is therefore overvalued relative to the vault's true post-loss state. This can make an unhealthy position 2 appear healthier than it is (skipping/softening liquidation), or cause the liquidator to seize less collateral than the true value requires, leaving additional bad debt that must be socialized later without ever being backed correctly — or cause a batch liquidation of position 2 to under-collateralize the seizure relative to actual vault share value, unfairly favoring the borrower at the liquidator/protocol's expense.
5. The mutation (`var-set lindex` in `socialize-debt`) is evaluated cross-contract mid-transaction, but the guard/read path (`resolve-ztoken`/`get-cached-indexes`) that should reflect it is a value cached and locked before the mutation happened — the classic "mutation evaluated before its guard/read" and "cached value not invalidated when its source moves" pattern, occurring entirely within one transaction under Clarity's left-to-right, strictly ordered evaluation.

### Impact Explanation
This falls into High/temporary-freezing or Critical/insolvency-adjacent territory: valuing zToken collateral with a stale (higher) `lindex` after a loss has already been socialized means subsequent liquidations in the same batch either under-seize collateral relative to true value or fail to trigger when they should, directly worsening protocol insolvency (uncollected bad debt) and skewing liquidation outcomes for the affected borrower/liquidator within that transaction. Since `lindex` also feeds every zToken price used in `get-notional-evaluation`/health checks for that block, incorrect valuation directly undermines liquidation correctness for all positions holding that zToken processed after the socialization event in the same transaction.

### Likelihood Explanation
Requires (a) a `liquidate-multi` (or similar chained/batched) call where multiple positions share the same zToken collateral asset, and (b) at least one earlier position triggering bad-debt socialization on that vault. Bad debt socialization is a routine, expected occurrence in undercollateralized liquidation scenarios, and `liquidate-multi` explicitly documents batching to avoid "front-running attacks that prevent bad debt socialization" — meaning the exact sequence (socialize then reuse cache) is an anticipated code path, making this reachable under normal liquidation-storm conditions (e.g., a market crash triggering many simultaneous bad-debt liquidations on the same vault).

### Recommendation
Invalidate or bypass the `index-cache` entry for a vault immediately after any call that can mutate its `lindex` (e.g., after `socialize-debt`), or key/refresh the cache by a per-write version/nonce rather than solely by `stacks-block-time`, ensuring any read of `get-cached-indexes` after a socialization event within the same transaction reflects the updated `lindex`.

### Proof of Concept
1. Set up two borrowers, A and B, each with `zUSDC` collateral backed by the same `vault-usdc`, both eligible for full liquidation.
2. Call `liquidate-multi` with A listed first, B second, both liquidations targeting `zUSDC` collateral / a debt asset.
3. In processing A: `liquidate` accrues/caches `vault-usdc`'s `{index, lindex}` for the current `stacks-block-time`; A's collateral is fully seized, remaining debt triggers `socialize-debt` on `vault-usdc`, writing a lower `lindex`.
4. In processing B (same transaction): `liquidate` calls `resolve-ztoken` for `zUSDC`, which reads the cached (pre-loss, higher) `lindex` instead of the updated one, producing an inflated collateral valuation for B and an incorrect liquidation outcome (either health check passes when it should fail, or seized collateral amount is computed against the wrong `lindex`).
5. Observe (via `print` events / return values) that B's `position-collateral-usd-before`/seized amounts are computed with the stale `lindex`, diverging from `vault-usdc`'s actual post-socialization state.

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

**File:** local-testing/contracts/market/market.clar (L365-369)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```

**File:** local-testing/contracts/market/market.clar (L1557-1583)
```text
      ;; Handle bad debt socialization if no collateral left
      (let ((bad-debt-socialized 
              (if no-collateral-left
                  (let ((stripped-debt-list (filter-out-debt-asset (get debt pos-full) debt-aid))
                        (fresh-debt-list (if (is-eq debt-updated u0)
                                             stripped-debt-list
                                             (unwrap-panic (as-max-len?
                                               (append stripped-debt-list
                                                       { aid: debt-aid, scaled: debt-updated })
                                               u64)))))
                    (if (> (len fresh-debt-list) u0) ;; if still has debt
                      (let ((socialization-result (fold socialize-debt-asset 
                                                        fresh-debt-list 
                                                        { borrower: borrower, success: true })))
                        (asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)
                        ;; emit bad-debt-socialized event
                        (print {
                          action: "bad-debt-socialized",
                          caller: contract-caller,
                          data: {
                            borrower: borrower,
                            debt-list: fresh-debt-list
                          }
                        })
                        true)
                      false))
                  false)))
```

**File:** local-testing/contracts/vault/vault-ststx.clar (L967-970)
```text
    (var-set lindex new-lindex)
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1593-1599)
```text
(define-public (liquidate-multi
                (positions (list 64 { borrower: principal,
                                      collateral-ft: <ft-trait>,
                                      debt-ft: <ft-trait>,
                                      debt-amount: uint,
                                      min-collateral-expected: uint })))
  (ok (map call-liquidate positions)))
```

**File:** local-testing/contracts/vault/vault-usdc.clar (L946-970)
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
```
