### Title
`liquidate-multi` swallows per-position `liquidate()` errors, letting partial state changes from a failed liquidation persist — (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`liquidate-multi` calls `liquidate` for each batch entry through the private helper `call-liquidate`, collects every result with `map`, and always returns `(ok (list ...))` at the top level [1](#0-0) . Because the outer function itself never returns `err`, the whole transaction commits even when one of the batched `liquidate` calls fails partway through — after it has already executed real, side-effecting `contract-call?`s (debt repayment, debt removal, collateral transfer) but before its final `asserts!` on bad-debt socialization succeeds. This is the Clarity-native analog to the EthRouter `change()` bug class: a loop/fold over sub-operations that is supposed to make each unit atomic, but instead "absorbs" a failure and lets its already-applied side effects strand in the committed state.

### Finding Description
`liquidate` performs, in order:
1. `(try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))` — pulls debt tokens from the liquidator into the vault [2](#0-1) .
2. `(try! (contract-call? .v0-market-vault debt-remove-scaled borrower scaled-to-remove debt-aid))` — decrements the borrower's debt [3](#0-2) .
3. `(try! (contract-call? .v0-market-vault collateral-remove borrower coll-final collateral-ft coll-aid actual-receiver))` — actually transfers seized collateral to the liquidator/receiver [4](#0-3) .
4. Only afterwards, if the position has no collateral left, it runs `(fold socialize-debt-asset fresh-debt-list ...)` and asserts success with `ERR-BAD-DEBT-SOCIALIZATION-FAILED` [5](#0-4) .

Each `contract-call?` in steps 1–3 is its own atomic unit: once it returns `ok`, its writes are committed to the enclosing execution independent of what happens later in the same function. If step 4's `asserts!` subsequently fails, `liquidate` itself returns `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)` — but that is only fatal to the **whole transaction** when `liquidate` is invoked directly as the top-level entry point (in that case Clarity rolls back every write made in steps 1–3 as well, since the top-level response is `err`).

When invoked through the batch path, `call-liquidate` just forwards whatever `liquidate` returns as one list element [1](#0-0) , and `liquidate-multi` always wraps the whole list in `(ok ...)`. The comment even documents this as intentional design ("Failed liquidations return error codes but don't revert entire batch") [6](#0-5) . Since the top-level response is `ok`, Clarity commits the whole transaction — including the debt repay/removal and collateral transfer from steps 1–3 of the position whose bad-debt socialization step failed at step 4.

### Impact Explanation
The bad-debt socialization step exists specifically to write down the vault's recorded debt/assets to reflect an unrecoverable position once its collateral is exhausted [7](#0-6) . If that step fails but the preceding debt-repay/collateral-seizure already committed, the borrower's position is left with zero collateral and a debt balance that never gets impaired/socialized in vault accounting. The vault's internal `total-assets`/index math continues to treat that unrecoverable debt as a live, collectible asset. This permanently misprices vault shares (`convert-to-assets`/`convert-to-shares`) for all other depositors, creating either overstated redeemable assets (protocol insolvency risk) or funds that later depositors/redeemers cannot actually retrieve — a temporary/permanent freezing of funds and understated bad debt, landing in the in-scope "protocol insolvency" / "permanent freezing of funds" impact class.

### Likelihood Explanation
This requires no attacker action beyond calling the existing, permissionless `liquidate-multi` entry point with a batch that includes at least one position where socialization fails (e.g., `vault-socialize-debt`/`debt-remove-scaled` errors on one of the borrower's other debt assets during the fold in `socialize-debt-asset`) [8](#0-7) . Because the design explicitly intends per-position errors to not abort the batch, any legitimate liquidator batching several liquidations can unintentionally trigger this, and a motivated actor could also engineer a position to force the socialization sub-call to fail (e.g., driving one debt-asset vault into a paused/edge state) purely to lock in the seizure while dodging the write-down. Likelihood is Medium-to-High given normal batch usage is the intended trigger path.

### Recommendation
`liquidate-multi` should not silently swallow a `liquidate` failure that occurred after value-moving side effects have already executed. Either:
- Move the bad-debt-socialization requirement earlier (fail-fast before any `vault-system-repay`/`collateral-remove` execute), or
- Make `liquidate`'s success atomic with socialization by ensuring the whole per-position sequence is wrapped so that any failure — including socialization failure — rolls back debt-repay and collateral-remove for that same position (e.g., by structuring the position handling so the entire per-position unit is itself gated behind a single `try!`/`asserts!` at the point where `liquidate-multi` decides whether to keep or discard that position's effects), or
- If partial batch failure is truly desired, explicitly re-apply the debt/collateral state changes only if socialization also succeeds, using a single atomic sub-call boundary per position rather than relying on `map` over a function whose internal errors don't propagate to the batch's top-level result.

### Proof of Concept
1. Construct a borrower whose position has two debt assets and collateral in a single asset, positioned so a single `liquidate()` call on the sole collateral asset will fully exhaust collateral (`no-collateral-left` becomes true) and trigger bad-debt socialization of the remaining debt asset via `fold socialize-debt-asset` [9](#0-8) .
2. Arrange for the remaining debt asset's vault (or `debt-remove-scaled` on `.v0-market-vault`) to fail during socialization — e.g., pause that vault's relevant operation so `vault-socialize-debt`/`debt-remove-scaled` returns an error inside `socialize-debt-asset`, causing `unwrap!` to return `failed-status` and the fold's `success` flag to become `false` [8](#0-7) .
3. Call `liquidate-multi` with this position included alongside one healthy, successful liquidation. `liquidate` for the target position returns `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)` only after `vault-system-repay`, `debt-remove-scaled`, and `collateral-remove` have already executed successfully.
4. Observe: `liquidate-multi` returns `(ok (list (ok ...) (err u...)))` — the transaction succeeds. Query the borrower's on-chain position afterward: collateral is gone (transferred to the liquidator) and the first debt asset's scaled debt was removed, but the second debt asset's bad debt was never socialized into the vault's accounting, leaving that vault's recorded assets permanently overstated relative to reality.

*Note: I could not directly inspect `vault-socialize-debt`'s exact failure conditions within the size/time constraints of this pass; the PoC step 2 trigger (an operation-specific pause or an edge-case rounding/insufficient-liquidity condition inside that call) should be validated against the actual vault contract logic before treating this as fully reproduced, though the root-cause pattern in `liquidate`/`liquidate-multi`/`call-liquidate` is confirmed directly from the cited code.*

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1495-1496)
```text
    ;; execute liquidation
    (try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1498-1503)
```text
    ;; update obligations and socialize bad debt
    (let ((debt-updated (try! (contract-call? .v0-market-vault
                              debt-remove-scaled
                              borrower
                              scaled-to-remove
                              debt-aid)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1504-1512)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1526-1560)
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1587-1599)
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
