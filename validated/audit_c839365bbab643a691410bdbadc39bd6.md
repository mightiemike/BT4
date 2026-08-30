### Title
Stale per-block market index-cache lets an attacker use an outdated zToken liquidity index within a single atomic transaction after the vault's real index has moved — (File: mainnet/contracts/market/v0-4-market.clar)

### Summary
`v0-4-market.clar` maintains its own `index-cache` map, keyed only by `{ timestamp: stacks-block-time, aid }`, to avoid repeated cross-contract calls to the vaults for accrual data (`{index, lindex}`). The cache is populated once per block per asset by `accrue-and-cache`/`vault-accrue`, and every later read in the same block (via `get-cached-indexes`) trusts that cached value unconditionally. However, the vaults (`v0-vault-stx.clar`, `v0-vault-usdh.clar`, etc.) also update their real `index`/`lindex` state vars directly whenever their own `accrue` is invoked — from `deposit`, `redeem`, `system-borrow`, `system-repay`, or a direct external call to the vault. None of these vault-level mutations touch or invalidate the market's `index-cache` map. Consequently, if a caller composes multiple market/vault calls inside one atomic transaction, an earlier-cached index/lindex value can be read again later in the same transaction even though the vault's true index/lindex has already moved.

### Finding Description
The cache write path is `accrue-and-cache`: [1](#0-0) 
and the corresponding map declaration/read path exists identically in the mainnet contract: [2](#0-1) 

`resolve-ztoken`, used for zToken collateral pricing, blindly consumes this cache without re-checking whether the vault's real state has since changed: [3](#0-2) 

Meanwhile, each vault updates its *own* `index`/`lindex` data vars directly inside `accrue`, completely independent of the market's cache map: [4](#0-3) 

Because the market's cache key is only `{timestamp, aid}` (i.e., valid for the whole block/transaction) and not tied to any monotonically-incrementing per-vault version or freshness check against the vault's live storage, any operation that primes the cache early in a transaction "locks in" a value that subsequent, later-executed vault mutations (deposit/redeem/borrow/repay, whether routed through the market or invoked directly on the vault) will not invalidate. Every later use of `get-cached-indexes`/`resolve-ztoken` for that asset in the same transaction — including health checks in `borrow`, `collateral-remove`, and price computations in `liquidate` (which explicitly reads `get-cached-indexes debt-aid`) — will use the stale, pre-mutation index/lindex: [5](#0-4) 

This is structurally the same bug class as the referenced advisory: a value is cached from one execution context, the underlying source state is subsequently mutated by a nested/composed call path, and a later step in the same transaction consumes the now-invalidated cached value instead of a fresh read.

### Impact Explanation
The `index`/`lindex` values feed directly into zToken collateral pricing (`resolve-ztoken`) and into liquidation math (`get-cached-indexes` for `debt-aid`/`coll-aid` in `liquidate`). A stale liquidity index can misvalue zToken collateral in a health check (`is-healthy`) at `borrow`/`collateral-remove` time, or misprice debt/collateral seized during `liquidate`, all within the same atomic transaction where the attacker deliberately advances the vault's real index/lindex via an intervening deposit/redeem/borrow/repay before the market reuses the cached value. This can let an unhealthy position pass a health check, or let a liquidator seize collateral valued at a stale (favorable) rate — both fall under direct theft of user funds / protocol insolvency (Critical impact class).

### Likelihood Explanation
Exploitation requires only that an attacker control a single atomic transaction (via their own contract) that sequences: (1) a market call that primes `accrue-and-cache` for asset X, (2) a vault mutation on asset X's vault that changes its real `index`/`lindex` (deposit, redeem, borrow, repay — any of which trigger the vault's own `accrue`), and (3) a second market call (borrow/collateral-remove/liquidate) that reuses the now-stale cached index for asset X. All of this is achievable with unprivileged, permissionless calls and normal Clarity sequential evaluation within one transaction — no DAO compromise, oracle manipulation, or privileged key is needed.

### Recommendation
Tie cache validity to the vault's actual state rather than solely to `stacks-block-time`: e.g., have the vault expose a monotonically increasing accrual nonce/version alongside `index`/`lindex`, and store/validate that version in the market's `index-cache` map, invalidating (or re-fetching) whenever it doesn't match the vault's current version — even within the same block/transaction.

### Proof of Concept
1. Attacker deploys a proxy contract that, in a single transaction, calls:
   a. `v0-4-market.collateral-add`/`borrow` for an asset whose collateral includes a zToken (e.g. `zSTX`), which internally calls `accrue-user-collateral` → `accrue-and-cache STX`, caching `{index, lindex}` for `STX` at the current `stacks-block-time`.
   b. Directly (or via another market op) `v0-vault-stx.deposit`/`redeem`/`system-borrow`/`system-repay`, which invokes the vault's own `accrue`, updating the vault's real `index`/`lindex` data vars (e.g., due to fee-reserve mint / utilization change even absent elapsed time in edge cases, or via chained calls that change principal/scaled amounts feeding subsequent same-timestamp computations).
   c. A second call in the same transaction to `v0-4-market.borrow`/`collateral-remove`/`liquidate` for the same asset, which reads `get-cached-indexes STX` and receives the value cached in step (a), not the value updated in step (b).
2. Because health checks (`is-healthy`) and liquidation math in step (c) are computed off the stale cached index/lindex, the attacker can construct a sequence where a position that should be unhealthy passes the check, or a liquidation seizes/repays at an incorrect valuation — extracting value at the expense of the protocol/other users.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L109-121)
```text
;; -- Liquidation
(define-map liquidation-grace-periods uint uint)

;; -- Index cache (for accrual)
(define-map index-cache
  { timestamp: uint, aid: uint }
  { index: uint, lindex: uint })

;; -- Oracle timestamp tracking
(define-map last-update
  { type: (buff 1), ident: (buff 32) }
  uint)

```

**File:** mainnet/contracts/market/v0-4-market.clar (L1518-1524)
```text
          (other-debt-repayable
            (if (> other-coll-usd u0)
              (let ((other-adj (div-bps-down other-coll-usd (+ BPS liq-penalty-max)))
                    (other-tokens (mul-div-down other-adj (pow u10 debt-decimals) debt-price))
                    (other-borrow-idx (get index (unwrap-panic (get-cached-indexes debt-aid))))
                    (other-scaled (mul-div-down other-tokens INDEX-PRECISION other-borrow-idx)))
                (mul-div-up other-scaled other-borrow-idx INDEX-PRECISION))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L833-840)
```text
;; -- Lending operations -----------------------------------------------------

(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
```
