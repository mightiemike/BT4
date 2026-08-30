### Title
Stale per-block liquidity-index cache in `market.clar` is not invalidated when a vault's `lindex` is rewritten by `socialize-debt`, causing zToken collateral mis-valuation for the rest of the block - (File: `local-testing/contracts/market/market.clar`, `local-testing/contracts/vault/vault-stx.clar` (and sibling vault contracts))

### Summary
`market.clar` caches each vault's `{index, lindex}` per `stacks-block-time` in the `index-cache` map to avoid repeated cross-contract accrual calls within a block [1](#0-0) . This cache is populated once (cache MISS → `vault-accrue`) and then trusted for the rest of the block, on the assumption that a vault's `index`/`lindex` only move forward through `accrue()`. However, `socialize-debt` writes `lindex` **directly**, independent of `accrue`/the cache, to mark down bad debt [2](#0-1) . Once a position's liquidation triggers bad-debt socialization on a vault, the market's cached `lindex` for that vault/timestamp becomes stale (too high) but is never invalidated, so any other operation resolving zToken collateral for the same vault, later in the same block, uses the pre-socialization (overstated) `lindex`.

### Finding Description
`accrue-and-cache` is the sole gate for populating `index-cache`: on a cache hit it just returns the stored value without re-checking the vault [1](#0-0) . zToken collateral valuation exclusively reads this cache via `get-cached-indexes`/`resolve-ztoken`, and if the cache is missing it errors out rather than falling back to a fresh vault read [3](#0-2) , confirming zToken pricing is entirely cache-driven within a block.

The cache's implicit invariant — "a vault's index/lindex for this block never change after the first accrual" — is violated by `socialize-debt`, which is called from `liquidate` when a borrower's position has no remaining collateral, and directly overwrites `lindex` to reflect the loss, bypassing `accrue` and any cache write [2](#0-1) . `liquidate` calls `accrue-user-collateral`/`accrue-user-debts` (which populate/hit the cache) *before* the debt/collateral notional valuation, and only calls `socialize-debt-asset` (→ `vault-socialize-debt`) *after* that valuation and the collateral transfer [4](#0-3) [5](#0-4) . This ordering is safe within that single liquidation call, but the resulting stale cache entry persists for the remainder of the block (`stacks-block-time` is the cache key) [6](#0-5) .

`liquidate-multi` batches several independent liquidations into one transaction via `map call-liquidate positions` [7](#0-6) , all sharing the same `stacks-block-time` and therefore the same `index-cache` entries. If an earlier position in the list liquidates fully and triggers `socialize-debt` on vault V (marking `lindex` down), and a later position in the same batch holds a zToken backed by vault V as collateral, that later position's `resolve-ztoken` call reads the now-stale, pre-socialization `lindex` still sitting in `index-cache` for vault V at this timestamp — overvaluing that collateral for the rest of the transaction [3](#0-2) . The same staleness affects any other transaction in the same block (same `stacks-block-time`) that resolves zToken-V collateral after the socialization tx, e.g. `collateral-add`, `borrow`, or a subsequent `liquidate` call on a different borrower.

This is a direct analog of the reported class: a value (`versionedHashes`/here, the cached `lindex`) is computed and cached at one point, its true source is mutated later (blob hashes recomputed / `lindex` marked down by `socialize-debt`), and the stale cached value is used downstream without invalidation, producing an inconsistency between the actual protocol state and what is used for a critical check (payload validation / collateral valuation).

### Impact Explanation
Overvaluing zToken collateral due to a stale `lindex` after a bad-debt write-down means:
- A borrower whose zToken-V collateral should be worth less (post-socialization) is valued higher than reality, letting them evade being flagged for liquidation, or letting a liquidator seize less collateral than the true post-loss ratio requires.
- Users can borrow more against the same zToken collateral than the true (marked-down) backing supports.

Either outcome allows extraction of value beyond what the underlying vault actually holds, which can permanently impair other suppliers' claims once the true state is reconciled — this falls under **temporary/permanent freezing or loss of funds at rest** (protocol insolvency risk for vault V's depositors), matching the in-scope Critical/High impact classes.

### Likelihood Explanation
Requires: (1) a liquidation that triggers `socialize-debt` (bad debt with zero remaining collateral) [8](#0-7) , and (2) another position holding the same vault's zToken as collateral being processed in the same block/timestamp. `liquidate-multi` makes this trivially achievable within a single attacker-controlled transaction by ordering positions in the list, and it can also occur opportunistically across two transactions within the same block since caching is keyed only by `stacks-block-time`. No privileged access or DAO action is needed — an attacker/liquidator fully controls both the triggering liquidation and the ordering of `liquidate-multi` entries.

### Recommendation
Invalidate or bypass the `index-cache` entry for a vault whenever `socialize-debt` is invoked on it within the same transaction/block — e.g., have `vault-socialize-debt`/`socialize-debt-asset` also clear (or refresh) the corresponding `index-cache` map entry in `market.clar`, or have `resolve-ztoken`/`get-cached-indexes` re-validate against a live vault read rather than trusting the block-scoped cache unconditionally once a socialization event has occurred in that block.

### Proof of Concept
1. Vault V (e.g. `vault-stx`) has borrower A with zToken-V collateral and a position that also has an outstanding debt on a different asset, leaving zero non-zToken collateral once liquidated.
2. Borrower B holds zToken-V as collateral for their own debt position.
3. Liquidator calls `market.liquidate-multi` with a list: `[liquidate(A, ...) , liquidate(B, ...)]` (or two ordinary `liquidate` calls in the same block/`stacks-block-time`).
4. Processing A: `accrue-user-collateral`/`accrue-user-debts` prime `index-cache` for vault V at the current `stacks-block-time` [1](#0-0) ; A's position is fully liquidated with no collateral left, triggering `fold socialize-debt-asset` → `vault-socialize-debt` → vault V's `socialize-debt`, which marks `lindex` down directly [2](#0-1) , without touching `index-cache`.
5. Processing B (same list/same block): `accrue-user-collateral` calls `accrue-and-cache` for vault V again — cache HIT for the same `{timestamp, aid}` key, so it returns the stale (pre-socialization, higher) `lindex` [1](#0-0) .
6. B's zToken-V collateral is priced via `resolve-ztoken` using this stale `lindex`, overvaluing B's collateral relative to the vault's true post-socialization state, letting B's position appear healthier than it truly is (avoiding liquidation, or enabling further borrowing) [3](#0-2) .

Note: full confirmation of every downstream valuation path (e.g. exact interplay with `get-notional-evaluation`) would benefit from running the existing test suite in `local-testing/tests/` against a crafted scenario; this was not executed as part of this analysis and is recommended before remediation.

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

**File:** local-testing/contracts/market/market.clar (L1428-1436)
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

**File:** local-testing/contracts/market/market.clar (L1549-1583)
```text
          (no-collateral-left (and
                                (is-eq coll-removed u0)
                                (or
                                  (is-eq (len (get collateral pos-full)) u1)
                                  (and
                                    (is-eq (len (get collateral pos-full)) (len (get collateral position)))
                                    (is-eq other-debt-repayable u0))))))

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

**File:** local-testing/contracts/market/market.clar (L1616-1622)
```text
(define-public (liquidate-multi
                (positions (list 64 { borrower: principal,
                                      collateral-ft: <ft-trait>,
                                      debt-ft: <ft-trait>,
                                      debt-amount: uint,
                                      min-collateral-expected: uint })))
  (ok (map call-liquidate positions)))
```

**File:** local-testing/contracts/vault/vault-stx.clar (L944-967)
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

```
