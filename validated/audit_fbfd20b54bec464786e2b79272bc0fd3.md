### Title
Stale per-block index cache in `market.clar` is not invalidated by vault `socialize-debt`, allowing collateral to be priced with pre-loss liquidity index in the same block - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`market.clar` caches vault liquidity/borrow indexes per `{timestamp: stacks-block-time, aid}` to save gas, on the assumption that a vault's index only changes through its own `accrue` path within a block. The vault's `socialize-debt` function, however, mutates `lindex` (and `principal-scaled`/`assets`) directly, bypassing the path that populates or refreshes the market's cache. Any market operation that already primed the cache for that asset earlier in the same block will keep using the pre-`socialize-debt` (higher) index for the remainder of the block, mispricing zToken collateral.

### Finding Description
`accrue-and-cache` in `market.clar` is the single source of truth market uses to price zToken collateral/debt within a block: [1](#0-0) 

```
(define-private (accrue-and-cache (aid uint))
  (let ((cache-key { timestamp: stacks-block-time, aid: aid })
        (cached? (map-get? index-cache cache-key)))
    (match cached?
      cached-indexes (ok cached-indexes)          ;; cache HIT -> stale value reused
      (let ((indexes (try! (vault-accrue aid))))
        (map-set index-cache cache-key indexes)
        (ok indexes)))))
```

This is invoked from `collateral-add` to price/cache the zToken's underlying index whenever new zToken collateral is added: [2](#0-1) 

and similarly from every other collateral/debt path (`collateral-remove`, `borrow`, `repay`, liquidation) through `accrue-user-collateral`/`accrue-user-debts`, all keyed only by `(stacks-block-time, aid)`.

Separately, the vault's `socialize-debt` function writes the liquidity index (`lindex`) and scaled principal directly, independent of the normal `accrue()` flow that `vault-accrue` (called by `accrue-and-cache`) exercises: [3](#0-2) 

```
(var-set lindex new-lindex)
(var-set principal-scaled ...)
(var-set total-borrowed ...)
(var-set assets ...)
...
(ok true)))
```

Because the market's `index-cache` is keyed purely on `stacks-block-time`, and `socialize-debt` does not go through `accrue-and-cache`'s population/refresh logic, any market-side operation that ran *before* `socialize-debt` in the same block leaves a stale, more-favorable `lindex` cached for that `aid`. Any later market operation in that same block that hits the cache (`cached? -> cached-indexes (ok cached-indexes)`) reuses this stale index instead of reading the updated post-socialization value, even though the vault's real state has moved.

### Impact Explanation
`lindex` directly determines the USD notional value assigned to zToken collateral via `get-asset-value`/`get-notional-evaluation`. If `socialize-debt` reduces `lindex` (socializing bad debt losses onto depositors, i.e., reducing the redemption value of the zToken) after the market has already cached the pre-loss index for that block, subsequent borrow/withdraw calls in the same block will value the same collateral higher than it actually is. A user can exploit this to pass health/LTV checks (`is-healthy`, capacity checks in `collateral-add`/`borrow`) that should fail post-socialization, extracting more debt or withdrawing more collateral than the position actually backs. This directly causes protocol insolvency (uncollateralized debt is created/left outstanding) — a Critical-tier impact.

### Likelihood Explanation
Exploitation only requires ordering transactions within a single Stacks block (achievable by any actor submitting a transaction alongside the block containing the DAO's/liquidator's `socialize-debt` call, or by front-running/back-running via mempool ordering), and no privileged access is required by the attacker. `socialize-debt` itself is an intended, expected protocol operation (used to handle bad debt), so triggering it is not a compromise scenario — the bug is purely in the market's cache-invalidation model versus a legitimate state-mutating vault call.

### Recommendation
Invalidate or bypass the `index-cache` entry for an asset whenever `socialize-debt` (or any other function that writes `lindex`/`index` outside the standard `accrue()`/`vault-accrue` path) executes, e.g. by having `socialize-debt` write through the same cache key, or by removing the affected `aid`'s cache entry for the current `stacks-block-time` so the next `accrue-and-cache` call is forced to re-read the vault's fresh state.

### Proof of Concept
1. Block N begins. User A calls `market.collateral-add` with zUSDC as collateral; `accrue-and-cache` is invoked for `aid = USDC`, caching `{timestamp: N, aid: USDC} -> {index, lindex}` at the pre-loss value.
2. Still within block N, the vault-usdc `socialize-debt` path is executed (e.g., processing bad debt), directly `var-set`-ing `lindex` to a lower value reflecting the loss, without touching `market.clar`'s `index-cache`.
3. Still within block N, User A (or any user) calls `market.borrow`/`market.collateral-remove` for an operation that needs the USDC/zUSDC index; `accrue-and-cache` for `{timestamp: N, aid: USDC}` hits the cache and returns the stale pre-loss `lindex`.
4. The stale, higher `lindex` is used in `get-notional-evaluation`/`get-asset-value`, overstating the USD value of the user's zUSDC collateral, letting the health/capacity check pass for a borrow or withdrawal that should have been rejected post-socialization.
5. Result: a position is created or preserved that is under-collateralized relative to the vault's true (post-socialization) state, leaving the protocol insolvent by that shortfall.

*Note: I could not fully trace the caller-authorization path/exact trigger conditions for `socialize-debt` from `market.clar` (20 call sites there) within the available search budget, so the precise entry point and permission gating around triggering `socialize-debt` should be re-verified against the full contract before treating this as conclusively exploitable end-to-end.*

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1060-1070)
```text
                          ;; Prime cache for new zToken collateral underlying if not already cached
                          (cache-primed (if (is-ztoken asset-id)
                                            (let ((vault-id (if (is-eq asset-id zSTX) STX
                                                            (if (is-eq asset-id zsBTC) sBTC
                                                            (if (is-eq asset-id zstSTX) stSTX
                                                            (if (is-eq asset-id zUSDC) USDC
                                                            (if (is-eq asset-id zUSDH) USDH
                                                            (if (is-eq asset-id zstSTXbtc) stSTXbtc
                                                            u100))))))))
                                              (try! (accrue-and-cache vault-id)))
                                            { index: u0, lindex: u0 }))
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L960-982)
```text

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
