### Title
Per-block borrow-index cache is not refreshed after a vault-mutating repay inside batched liquidations, causing stale debt valuation for later positions in the same transaction - (File: mainnet/contracts/market/v0-4-market.clar)

### Summary
`market.clar` caches each vault's `{index, lindex}` in `index-cache` keyed only by `{ timestamp: stacks-block-time, aid }` [1](#0-0) . The cache is populated once per block via `accrue-and-cache`, which on a cache hit returns the stored value without re-querying the vault [2](#0-1) . Several downstream consumers (`convert-to-scaled-debt`, `scale-debt-for-liquidation`, `resolve-ztoken`) read this cache via `get-cached-indexes`, a bare `map-get?` that does **not** re-accrue [3](#0-2) [4](#0-3) [5](#0-4) . The protocol's own code proves this cache can go stale mid-transaction: `socialize-debt-asset` explicitly re-writes the cache after a vault-mutating call with the comment "Refresh cache with new indexes post-write-down (lindex decreased)" [6](#0-5) . No equivalent refresh exists after `vault-system-repay`/`vault-system-borrow` calls used by the ordinary `liquidate` path, which is invoked repeatedly inside a single batch transaction via `call-liquidate`/fold over a list of positions [7](#0-6) .

### Finding Description
1. A batch liquidation transaction folds `call-liquidate` over a list of `{ borrower, collateral-ft, debt-ft, debt-amount, min-collateral-expected }` entries, each internally invoking `liquidate` [7](#0-6) .
2. The first liquidation touching debt asset `X` triggers `accrue-and-cache(X)`. Since `index-cache` is keyed by `{timestamp: stacks-block-time, aid: X}` and there is no entry yet this block, it fetches a fresh `{index, lindex}` from the vault via `vault-accrue` and stores it in `index-cache` [2](#0-1) .
3. That first liquidation then calls `vault-system-repay` for asset `X`, mutating the real vault state (principal reduction, and potentially the vault's internal index bookkeeping) — the "source" the cached value was derived from has moved, but nothing re-writes `index-cache` for this ordinary repay path (unlike `socialize-debt-asset`, which explicitly does so at line 892-895).
4. A later liquidation in the *same* transaction/fold, touching the *same* debt asset `X` for a different borrower, calls `scale-debt-for-liquidation`, which reads the borrow index via `get-cached-indexes` — a plain `map-get?` on the identical `{timestamp, aid: X}` key [4](#0-3) . Because `stacks-block-time` has not changed within the transaction, this is a cache **hit** returning the value cached in step 2 — before the repay in step 3 changed the underlying vault state.
5. Under Clarity's sequential/deterministic evaluation, this interleaving (accrue→cache→mutate-via-repay→reuse-stale-cache) is guaranteed to occur whenever two fold iterations in one call touch the same debt asset, since there is no other invalidation trigger besides a block-timestamp change.

### Impact Explanation
Liquidation math (`scale-debt-for-liquidation`, `convert-to-scaled-debt`) converts between scaled and real debt using the borrow index. If a later liquidation in the same batch uses a stale, pre-repay index for asset `X` instead of the fresh one, `debt-to-repay` and the corresponding `coll-final` seized from the borrower are computed against an inconsistent state — resulting in liquidators seizing more or less collateral than the true post-repay debt justifies. This is a form of debt/collateral misvaluation caused entirely by this contract's own caching bug (not third-party oracle data), landing on the in-scope impact of theft of user funds at rest (over-seizure of collateral) or permanent mispricing/freezing of remaining debt obligations.

### Likelihood Explanation
Any batched liquidation call (or any sequence of operations within one transaction) that touches the same debt asset `aid` more than once will hit this path deterministically, since the cache key never changes within a block and no repay/borrow site besides `socialize-debt-asset` refreshes it. This does not require any privileged access, DAO action, or interference between unrelated users' unrelated calls — it is a single caller executing a single batch transaction (`call-liquidate` fold) that internally strands a stale cached index across iterations.

### Recommendation
After any vault-mutating call (`vault-system-borrow`, `vault-system-repay`, `vault-deposit`, `vault-redeem`) inside `liquidate` or any other public entry point, explicitly refresh `index-cache` for the affected `aid` the same way `socialize-debt-asset` already does at lines 892-895, or replace all direct `get-cached-indexes` reads in debt/liquidation math with `accrue-and-cache` calls so every read is guaranteed fresh relative to prior state-changing calls in the same transaction.

### Proof of Concept
1. Submit a batch-liquidation transaction with two entries, both having debt in asset `USDC` (aid 6), targeting borrowers A and B.
2. Iteration 1 (borrower A): `liquidate` → `accrue-and-cache(USDC)` caches `{index: I0, lindex: L0}` for `{timestamp: T, aid: 6}` → `vault-system-repay` reduces A's debt and (through the vault's internal accounting) moves the vault's true index state.
3. Iteration 2 (borrower B, same block, same `T`): `liquidate` → `scale-debt-for-liquidation` → `get-cached-indexes(6)` returns the stale `{index: I0, lindex: L0}` cached in step 2, ignoring any change caused by A's repay, producing an incorrect `scaled-to-remove`/`debt-to-repay`/`coll-final` for B's liquidation.
4. Result: B's liquidation collateral seizure/debt reduction is computed against a state that no longer matches the vault's ground truth, exactly matching the "cached value not invalidated when its source moves" bug class, evidenced by the developers' own manual cache-refresh workaround limited to `socialize-debt-asset` at lines 890-895.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L112-115)
```text
;; -- Index cache (for accrual)
(define-map index-cache
  { timestamp: uint, aid: uint }
  { index: uint, lindex: uint })
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

**File:** mainnet/contracts/market/v0-4-market.clar (L343-347)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L858-868)
```text
(define-private (scale-debt-for-liquidation
  (debt-final uint)
  (coll-actual uint)
  (curr-scaled uint)
  (asset-id uint))
  (let (;; convert debt amount to scaled units for storage
        (borrow-index (get index (unwrap-panic (get-cached-indexes asset-id))))
        (scaled-debt (mul-div-down debt-final INDEX-PRECISION borrow-index))
        ;; cap at current debt (prevent over-repayment)
        (scaled-to-remove (if (> scaled-debt curr-scaled) curr-scaled scaled-debt))
        (debt-to-repay (mul-div-up scaled-to-remove borrow-index INDEX-PRECISION))
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

**File:** mainnet/contracts/market/v0-4-market.clar (L907-918)
```text
(define-private (call-liquidate (position { borrower: principal,
                                            collateral-ft: <ft-trait>,
                                            debt-ft: <ft-trait>,
                                            debt-amount: uint,
                                            min-collateral-expected: uint }))
  (liquidate (get borrower position)
             (get collateral-ft position)
             (get debt-ft position)
             (get debt-amount position)
             (get min-collateral-expected position)
             none   ;; collateral-receiver defaults to liquidator
             none)) ;; price-feeds not supported in batch - update prices separately
```

**File:** mainnet/contracts/market/v0-4-market.clar (L944-945)
```text
(define-read-only (get-cached-indexes (aid uint))
  (map-get? index-cache { timestamp: stacks-block-time, aid: aid }))
```
