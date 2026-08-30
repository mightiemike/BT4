## Title
`liquidate-multi` silently commits state mutated by a `liquidate` call that itself returns an error - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`liquidate-multi` batches liquidations by calling `call-liquidate` for each position via `map`, and always returns `(ok (list ...))` regardless of whether any individual `liquidate` invocation errored out. `call-liquidate` invokes `liquidate` as a plain in-contract function call (not `contract-call?`), so `liquidate`'s internal `asserts!`/`try!` failures (e.g., the bad-debt-socialization assertion) return an `(err ...)` value that is captured as a list element rather than propagated to the top-level response. Because Clarity's atomic rollback of state changes is anchored to the ultimate response of the entry point invoked by the transaction (`liquidate-multi`, which is `ok`), any `contract-call?`s made earlier inside a "failed" `liquidate` execution (debt repayment, collateral seizure/transfer) have already committed independently and are not undone just because `liquidate`'s own local return value is `err`.

### Finding Description
`liquidate` performs several sequential steps, each crossing a `contract-call?` boundary that commits independently once it succeeds:
1. `(try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))` [1](#0-0) 
2. `(try! (contract-call? .v0-market-vault debt-remove-scaled borrower scaled-to-remove debt-aid))` and `(try! (contract-call? .v0-market-vault collateral-remove borrower coll-final collateral-ft coll-aid actual-receiver))` [2](#0-1) 
3. Only after these succeed does `liquidate` run bad-debt socialization via a `fold` over `socialize-debt-asset`, and then `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` [3](#0-2) 

If this final `asserts!` fails, `liquidate` (the function) returns `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)`. Crucially, `call-liquidate` calls `liquidate` directly — not through `contract-call?` — so this is a plain local function call: [4](#0-3) 

`liquidate-multi` then wraps the whole `map` output in `ok`, unconditionally: [5](#0-4) 

The comment explicitly documents intent to avoid reverting the entire batch on a single failed position: "Failed liquidations return error codes but don't revert entire batch." [6](#0-5) 

The bug is that this design conflates two different things: (a) not reverting *other* positions in the batch (fine), and (b) not reverting the *state already committed inside the failing position's own execution* (the `vault-system-repay`, `debt-remove-scaled`, and `collateral-remove` calls). Because the earlier `contract-call?`s inside the failing `liquidate` execution already committed as independent successful sub-transactions, and because `liquidate`'s own error return is absorbed by `map`/`ok` rather than propagated to the entry point, the debt reduction and collateral transfer to the liquidator persist even though the position is reported as an `err` in the returned list and bad-debt socialization never actually happened.

This matches the "fold that absorbs failure" / "multi-step entry point that strands value on abort" analog class: `liquidate-multi` is a multi-step entry point over multiple positions, and a partial abort inside one position's multi-step flow strands the earlier steps' side effects (collateral transferred, debt reduced) as committed, while socialization of any remaining bad debt is skipped.

### Impact Explanation
This falls under "temporary/permanent freezing of funds" and, more importantly, "protocol insolvency" / "theft of funds": bad debt that should have been socialized (written down against vault liquidity) is left unaccounted for in the vault's global debt/asset bookkeeping while the liquidator has already walked away with collateral and the borrower's registered debt has already been reduced in `market-vault`. This creates a permanent accounting mismatch between vault-tracked `total-borrowed`/`assets` and the actual debt still owed, silently degrading vault solvency for depositors while the batch call reports the position as failed. Because the failure path is designed to be "tolerated" by the caller (that's the whole point of `liquidate-multi`), this class of desync is systematically reachable, not a one-off edge case.

### Likelihood Explanation
The failure path that triggers this — `ERR-BAD-DEBT-SOCIALIZATION-FAILED` — is reached whenever, after collateral is fully seized and no collateral remains, the socialization fold's inner `contract-call?`s (`vault-socialize-debt`, `vault-accrue`, or `debt-remove-scaled`) return an error for any of the borrower's remaining debt assets. This is a call any liquidator can trigger simply by choosing an underwater position via `liquidate-multi` where full collateral is being seized, making it a liquidator-controlled condition rather than a rare or adversarial-only scenario.

### Recommendation
Ensure `liquidate-multi` correctly reflects atomicity per-position: either (a) genuinely require each `liquidate` invocation inside the batch to be all-or-nothing internally (perform bad-debt socialization checks and mutations *before* any collateral/debt-reducing `contract-call?`s execute, or use `contract-call?` — even to `.self` — around `liquidate` so a per-position error rolls back that position's own prior contract-calls), or (b) if partial success within a position is intentionally allowed, explicitly re-verify and repair vault accounting consistency (e.g., don't allow `no-collateral-left` handling to skip if socialization fails; instead, fail the whole position atomically before any transfer occurs).

### Proof of Concept
1. A borrower's position becomes fully liquidatable with a single collateral asset and multiple debt assets outstanding.
2. A liquidator calls `liquidate-multi` with a batch entry targeting this borrower, seizing all their collateral in one call.
3. Inside `liquidate`: `vault-system-repay`, `debt-remove-scaled`, and `collateral-remove` all succeed and commit (collateral now with liquidator, targeted debt asset's obligation reduced). [7](#0-6) 
4. `no-collateral-left` evaluates true, triggering the bad-debt socialization fold over the borrower's remaining debt assets. [8](#0-7) 
5. One of the remaining debt asset's socialization sub-calls (`vault-socialize-debt`/`vault-accrue`/`debt-remove-scaled`) errors (e.g., due to a vault-side guard), causing the fold's accumulator to record `success: false` for the remainder of the fold. [9](#0-8) 
6. `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` fails, causing `liquidate` to return `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)`. [3](#0-2) 
7. `call-liquidate` returns this err value as-is (no `try!`), and `liquidate-multi`'s `(ok (map call-liquidate positions))` returns `ok` for the whole transaction. [4](#0-3) [5](#0-4) 
8. Result: the collateral transfer to the liquidator and the debt reduction from step 3 remain committed on-chain, while the remaining debt was never socialized into the vault's accounting — producing a permanent solvency mismatch, even though the batch entry for this position reports as `err`.

Note: I was unable to execute an actual local/integration test to empirically confirm Clarity's precise state-commit granularity for a locally-called (non-`contract-call?`) function that itself performs earlier successful `contract-call?`s before failing; this analysis is based on the documented Clarity semantics that atomic rollback is anchored at contract-call boundaries and at the outermost transaction entry point's final response, combined with the code's structure. I recommend verifying this exact interleaving with a Clarinet/clarigen integration test before treating it as fully confirmed.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L879-903)
```text
(define-private (socialize-debt-asset
                (debt-entry { aid: uint, scaled: uint })
                (acc { borrower: principal, success: bool }))
  ;; Early return if previous socialization failed
  (if (not (get success acc))
      acc
      (let ((borrower (get borrower acc))
            (failed-status { borrower: borrower, success: false })
            (asset-id (get aid debt-entry))
            (scaled-debt (get scaled debt-entry)))

            ;; Socialize in vault - pass scaled directly to avoid rounding
            (unwrap! (vault-socialize-debt asset-id scaled-debt) failed-status)
            ;; Refresh cache with new indexes post-write-down (lindex decreased)
            (map-set index-cache
                     { timestamp: stacks-block-time, aid: asset-id }
                     (unwrap! (vault-accrue asset-id) failed-status))
            ;; Remove from obligation
            (unwrap! (contract-call? .v0-market-vault
                                      debt-remove-scaled
                                      borrower
                                      scaled-debt
                                      asset-id) failed-status)
          acc)
        ))
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1495-1512)
```text
    ;; execute liquidation
    (try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))

    ;; update obligations and socialize bad debt
    (let ((debt-updated (try! (contract-call? .v0-market-vault
                              debt-remove-scaled
                              borrower
                              scaled-to-remove
                              debt-aid)))
          ;; Collateral receiver defaults to liquidator if not specified
          (actual-receiver (match collateral-receiver recv recv liquidator))
          (coll-removed (try! (contract-call? .v0-market-vault
                              collateral-remove
                              borrower
                              coll-final
                              collateral-ft
                              coll-aid
                              actual-receiver)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1526-1548)
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
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1591-1599)
```text
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
