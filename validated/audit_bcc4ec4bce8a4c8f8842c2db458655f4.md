### Title
Partial liquidation state persists when bad-debt socialization fails inside `liquidate-multi` batch calls - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`liquidate-multi` calls `liquidate` **directly** (an ordinary intra-contract function call, not a `contract-call?`) once per position via `call-liquidate`, and wraps every individual result in a list so that "failed liquidations return error codes but don't revert entire batch" by design. However, `liquidate` itself performs several state-mutating `contract-call?`s (debt repay, `debt-remove-scaled`, `collateral-remove`) *before* it reaches the final bad-debt-socialization step, which uses a fold (`socialize-debt-asset`) that silently absorbs the failure of any individual asset's `vault-socialize-debt` call. When that fold's aggregate `success` flag comes back false, `liquidate` aborts via `asserts!` and returns `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)` — but because this return happens inside a *direct* function call (not a `contract-call?` boundary), the previously-succeeded `contract-call?`s from earlier in the same `liquidate` invocation (repay, debt removal, collateral seizure to the liquidator) are not undone. Since `liquidate-multi` itself always returns `(ok (list ...))`, the top-level transaction commits, permanently keeping the partial, inconsistent state.

### Finding Description
1. A liquidator calls `liquidate-multi` with one or more positions [1](#0-0) .
2. `liquidate-multi` maps `call-liquidate` over the list, which calls `liquidate` **directly** (no `contract-call?` boundary between them, since both live in the same contract) [2](#0-1) .
3. Inside `liquidate` for a given position, several `contract-call?`s already succeed and commit their effects as part of the ongoing top-level transaction: `vault-system-repay`, `market-vault.debt-remove-scaled`, and `market-vault.collateral-remove` (which sends seized collateral to the liquidator) [3](#0-2) .
4. If the position now has no collateral left, `liquidate` attempts to socialize any remaining debt via `(fold socialize-debt-asset fresh-debt-list ...)` [4](#0-3) .
5. `socialize-debt-asset` is a fold that "absorbs failure": once one asset's socialization fails, `unwrap!` returns a `failed-status` for that call, and every subsequent list item is passed through unchanged via `(if (not (get success acc)) acc ...)` [5](#0-4) .
6. Back in `liquidate`, `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` fires and `liquidate` returns `(err ...)` [6](#0-5) .
7. Because this `err` is a plain function return value (not crossing a `contract-call?` boundary), it does **not** roll back the already-committed effects from step 3 for that position. `call-liquidate`/`map` simply records this `err` as one entry in the result list; `liquidate-multi` still returns `(ok (list ...))`, so the whole transaction commits with the partial mutations intact [7](#0-6) .

The end state: the borrower's collateral for that asset is gone (sent to the liquidator) and the primary debt entry was removed, but the *other* remaining debt on the position was never socialized/written down in the corresponding vault(s) — it is left outstanding and permanently uncollateralized, with no vault accounting adjustment (`socialize-debt`) to compensate lenders for the loss.

### Impact Explanation
This corrupts the market's collateral/debt invariant: debt is left outstanding against a position with zero collateral, and the vault's `total-borrowed`/`assets`/`lindex` accounting is never adjusted to reflect that this debt is now unrecoverable bad debt. Because collateral already flowed to the liquidator irreversibly, depositors in that vault cannot ever be made whole for the corresponding shortfall — this is a permanent freezing/loss of LP funds (protocol insolvency for the affected vault), landing in the in-scope "permanent freezing of funds" / "protocol insolvency" impact class.

### Likelihood Explanation
Likelihood is moderate-to-high: any borrower position with multiple debt assets and a liquidation path that removes their last unit of collateral is exposed. `vault-socialize-debt` can fail for ordinary reasons (e.g., authorization/caller checks, pause state on a specific vault, or `ERR-AMOUNT-ZERO` when `scaled-debt` is `u0` for some entry) — no privileged access is required to trigger the failure, and calling via `liquidate-multi` (a standard public entrypoint) is enough to expose the bug; a liquidator does not even need to intend it.

### Recommendation
`liquidate` should not rely solely on `asserts!`/fold-absorption for bad-debt socialization when invoked from a batch context that itself never propagates the error at the top level. Either (a) require the whole `liquidate-multi` transaction to abort on any bad-debt-socialization failure (removing the "don't revert entire batch" design for this specific failure path), or (b) restructure `liquidate` so the debt/collateral removal and bad-debt socialization happen atomically with respect to each other — e.g., perform socialization checks/pre-conditions before executing `debt-remove-scaled`/`collateral-remove`, so a failure there cannot leave collateral already disbursed while debt write-down fails.

### Proof of Concept
1. Set up a borrower position with two debt assets (A and B) and collateral fully backing both, such that liquidating asset A's debt consumes all of the position's collateral (`no-collateral-left` becomes true).
2. Ensure asset B's vault is paused for socialization (or otherwise cause `vault-socialize-debt` for asset B to return an error) while asset A's vault is not.
3. Call `liquidate-multi` with a single position targeting debt asset A.
4. Inside `liquidate`: `vault-system-repay`, `debt-remove-scaled`, and `collateral-remove` for asset A all succeed and commit (collateral sent to liquidator, asset-A debt cleared). The socialization fold over `fresh-debt-list` (which now includes asset B, since it's still open debt) succeeds for early no-op case then fails on the errored vault call for B, returning `success: false`.
5. `asserts!` fires inside `liquidate`, returning `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)`.
6. `call-liquidate`/`map` records this as an `err` entry; `liquidate-multi` returns `(ok (list (err ERR-BAD-DEBT-SOCIALIZATION-FAILED)))`.
7. Inspect on-chain state after the transaction: borrower's collateral for the liquidated asset is gone, asset-A debt is cleared, but asset-B debt remains outstanding with zero backing collateral, and vault B's `total-borrowed`/`assets` were never adjusted — confirming the stranded, uncollateralized, unsocialized debt.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1534-1548)
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
