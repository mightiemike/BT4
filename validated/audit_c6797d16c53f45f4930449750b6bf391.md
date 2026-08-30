### Title
Stale Index Cache Not Invalidated by Same-Block Debt Socialization - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
The market's vault-index cache (`index-cache`) is keyed only by `{ timestamp: stacks-block-time, aid }`, on the assumption that a vault's liquidity index can only change when time advances. However, `vault-socialize-debt` mutates a vault's underlying accounting (writing off bad debt onto remaining suppliers) instantly, without any block-time change. If a socialize-debt event (via liquidation) occurs in the same block after the index for that asset has already been cached, every subsequent operation in that block that consults the cache (`accrue-and-cache`) will keep using the pre-loss index instead of the post-loss one, mispricing that vault's zToken collateral for the remainder of the block.

### Finding Description
`accrue-and-cache` caches the vault's `(index, lindex)` under a key derived solely from `stacks-block-time` and `aid`: [1](#0-0) 

On a cache hit it returns the previously stored value without re-consulting the vault at all: [2](#0-1) 

This is used both for debt accrual and for zToken collateral pricing (e.g., during `collateral-add`, where a fresh vault index is primed into the cache before the health check): [3](#0-2) 

The comment "Cache invalidated each block" / "eliminates stale data risks" assumes the only thing that can change a vault's index is elapsed time (interest accrual). But `vault-socialize-debt` can instantly change a vault's total-debt-to-total-shares ratio (i.e., its index) by writing off bad debt onto suppliers, independent of any time delta: [4](#0-3) 

Because the cache key contains no vault-state component (only `stacks-block-time`), once an index for `aid` has been written to `index-cache` in the current block, a socialize-debt event later in that same block does not invalidate it. Any market operation that resolves prices/collateral values for a zToken tied to that vault later in the same block will read the now-stale (higher, pre-loss) index via `get-cached-indexes`/`accrue-and-cache`, rather than the correct post-loss value: [5](#0-4) 

### Impact Explanation
An overvalued zToken collateral index lets a borrower's health/notional calculations (`get-notional-evaluation`, capacity checks in `collateral-add`/`borrow`) treat the position as healthier than it actually is after the loss has been socialized. This can allow a user to borrow beyond what their now-diminished collateral actually supports, or to evade liquidation that should otherwise trigger, within the same block the loss occurred. That is a path to protocol insolvency (bad debt is under-collateralized relative to real vault state) — a Critical-class impact per the scope's definition of protocol insolvency.

### Likelihood Explanation
This requires a socialize-debt event (triggered through liquidation of bad debt) to occur in the same block as, and after, a prior cache-priming call for the same `aid`, followed by another market operation depending on that cached index in the same block. This is plausible in busy blocks or can be intentionally engineered by an attacker who front-runs/back-runs a socialize-debt-triggering liquidation with their own collateral-add/borrow calls in the same block, since Stacks block production allows multiple transactions per block and Clarity evaluation order within/across transactions in a block is deterministic and attacker-influenceable via transaction ordering/fees.

### Recommendation
Invalidate (or bypass) the `index-cache` entry for an asset whenever `vault-socialize-debt` (or any other non-time-driven state mutation affecting the vault's index) executes, e.g., by calling `map-delete` on the corresponding `index-cache` key(s) for that `aid` immediately after `vault-socialize-debt`, or by including a vault-state-derived nonce/version in the cache key instead of relying solely on `stacks-block-time`.

### Proof of Concept
1. Block N, Tx 1: User A calls `collateral-add` with a zToken collateral whose underlying vault is `aid`. This primes `index-cache` at `{ timestamp: T, aid }` with the current (healthy) index via `accrue-and-cache` (`mainnet/contracts/market/v0-4-market.clar:1061-1070`).
2. Block N, Tx 2: A liquidation on that same vault triggers `vault-socialize-debt`, writing off bad debt and instantly reducing the true value backing vault shares (`mainnet/contracts/market/v0-4-market.clar:216-223`) — but `index-cache` at `{ timestamp: T, aid }` is untouched.
3. Block N, Tx 3: User A (or a colluding account) calls `borrow` or another `collateral-add`, and the health/notional check calls `accrue-and-cache aid`, hitting the cache and receiving the stale pre-socialization index (`mainnet/contracts/market/v0-4-market.clar:249-252`), overvaluing the zToken collateral.
4. User A borrows against collateral that, after the socialized loss, no longer supports that debt level, or successfully avoids being flagged unhealthy for that block — a liability the protocol must absorb, i.e., unaccounted-for bad debt/insolvency risk.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L944-945)
```text
(define-read-only (get-cached-indexes (aid uint))
  (map-get? index-cache { timestamp: stacks-block-time, aid: aid }))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1061-1070)
```text
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
