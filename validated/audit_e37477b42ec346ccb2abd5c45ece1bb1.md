## Finding

### Title
`liquidate-multi` swallows a failed `liquidate()` call, letting a partially-executed liquidation (repay, debt-removal, collateral-seizure, and vault bad-debt write-down) commit even though the position's bad-debt socialization step failed - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`liquidate-multi` invokes `liquidate` for each position through a plain (non-`contract-call?`) intra-contract function call wrapped in `map`, and unconditionally returns `(ok (list ...))` regardless of whether individual `liquidate` calls returned `(err ...)`. Because Clarity's automatic rollback-on-error applies to the *outermost* function invoked by the transaction (and to each individual `contract-call?` sub-invocation), but not to a plain nested function call whose error is caught and discarded by the caller, any state mutations that `liquidate` already committed via its own successful `contract-call?`s (debt removal, collateral seizure, repay, and the vault's bad-debt write-down) survive even when `liquidate` itself ultimately returns an error and the batch as a whole reports success.

### Finding Description
`liquidate` performs several *individually atomic* `contract-call?`s before the point where it can still fail:
1. `(try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))` [1](#0-0) 
2. `debt-remove-scaled` and `collateral-remove` against `.v0-market-vault` [2](#0-1) 
3. If no collateral remains, a bad-debt socialization `fold` over `socialize-debt-asset`, which itself performs a `vault-socialize-debt` call (writing down the vault's `lindex`, i.e. spreading the loss over all depositors) and a `vault-accrue`/`debt-remove-scaled` call per debt entry, short-circuiting on the first failure but *not* undoing the socialize-debt call that already ran [3](#0-2) 

If any entry in that fold fails, `socialization-result`'s `success` flag is `false`, and:
```
(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)
```
makes `liquidate` itself return `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)` [4](#0-3) .

When a user calls `liquidate` directly (top-level transaction), this err return triggers Clarity's standard all-or-nothing rollback of the entire transaction, undoing steps 1-3 above — safe as intended.

However, `liquidate-multi` calls `liquidate` indirectly through `call-liquidate`, using plain function application (not `contract-call?`) inside a `map`, and always returns `(ok ...)`:
```
(define-public (liquidate-multi (positions (list 64 {...})))
  (ok (map call-liquidate positions)))
``` [5](#0-4) 
```
(define-private (call-liquidate (position {...}))
  (liquidate (get borrower position) ...))
``` [6](#0-5) 

Since `liquidate-multi` (the top-level transaction entry point) always returns `(ok ...)`, the whole transaction commits — including every already-successful nested `contract-call?` made by a `liquidate` invocation that later failed internally. The failure is only reflected as an `err` value inside the returned result list, matching the documented intent ("Failed liquidations return error codes but don't revert entire batch") [7](#0-6) , but that design does not account for the fact that `liquidate`'s own internal steps are not individually reversible once they've each returned `ok`.

### Impact Explanation
For a position with `no-collateral-left == true`, a caller can trigger `liquidate-multi` (a single-block, single-transaction call) targeting a borrower whose bad-debt socialization fails partway through the fold (e.g., because one asset among several debt entries hits a downstream failure in `vault-accrue`/`debt-remove-scaled`). The outcome:
- The liquidator's repay, the borrower's debt-removal, and collateral seizure already committed via successful sub-calls.
- The vault's `lindex` has already been written down for at least the entries processed before the fold's first failure (loss socialized onto depositors), while the corresponding debt bookkeeping for the failed/unprocessed entries is never cleaned up.
- The batch call still reports overall success, and none of this is rolled back.

This produces a permanent accounting divergence between the market-vault's user debt ledger and the vault's total-borrowed/lindex state — i.e. protocol insolvency / permanent freezing of funds, since the true backing for outstanding zToken shares no longer matches recorded per-vault totals, and this cannot be corrected by a retry (the affected debt entries are already partially processed).

### Likelihood Explanation
This does not require any privileged role — any account is permitted to call `liquidate-multi` on any undercollateralized position. It requires only a scenario where bad-debt socialization for a fully-seized position partially fails (e.g., a debt entry causing `vault-accrue`/`debt-remove-scaled` to error while an earlier entry's `vault-socialize-debt` already succeeded), which is realistic whenever a borrower carries multiple debt assets during a full liquidation.

### Recommendation
`liquidate-multi` should not silently swallow a failed `liquidate()`; either (a) make each per-position liquidation atomic by having `call-liquidate` invoke `liquidate` in a way that is transactionally isolated (e.g. via a genuine `contract-call?` boundary) so that a failing liquidation fully rolls back its own side effects, or (b) restructure `socialize-debt-asset` and the surrounding logic so that no external state mutation (`vault-socialize-debt`, `vault-accrue`, `debt-remove-scaled`) is committed until the entire socialization fold is known to succeed (compute-then-commit pattern), removing any window where partial socialization state can persist.

### Proof of Concept
1. Borrower has collateral fully liquidatable and debt spread across ≥2 assets such that after collateral seizure, `no-collateral-left` is `true` and `fresh-debt-list` has 2+ entries.
2. Craft/await conditions so the first entry's `vault-socialize-debt` succeeds (writing down that vault's `lindex`) but the second entry's `vault-accrue`/`debt-remove-scaled` call fails (e.g., transient state causing an assertion inside `vault-accrue` to fail).
3. Call `liquidate-multi` with this position included. `call-liquidate`→`liquidate` executes steps 1495-1512 successfully (repay, debt-remove-scaled, collateral-remove), then the fold at 1545-1548 fails on the second entry, causing `liquidate` to return `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)`.
4. `map` collects this `err` in the result list; `liquidate-multi` still returns `(ok (list ... err ...))`, and the transaction commits.
5. Result: the vault backing the first debt asset is permanently written down (loss socialized) while the borrower's debt-ledger for the second asset was never cleaned up and the liquidator/borrower state is left partially processed — with no transaction-level revert to undo it.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1495-1496)
```text
    ;; execute liquidation
    (try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1499-1512)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1545-1548)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1611-1616)
```text
                (receiver (optional principal))
                (price-feeds (optional (list 3 (buff 8192)))))
  (let ((coll-address (contract-of collateral-ft))
        (coll-asset (try! (get-asset coll-address)))
        (ztoken-id (get id coll-asset))
        ;; Map zToken to underlying vault ID for redemption
```
