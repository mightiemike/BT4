### Title
`liquidate-multi` swallows per-position liquidation errors after collateral/debt mutations have already been committed - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593-1599) maps `call-liquidate` over a batch of positions and unconditionally wraps the result as `(ok (map call-liquidate positions))`. Because the outer transaction always returns `ok`, Clarity's transaction-level rollback never triggers, even when an individual `call-liquidate` → `liquidate` invocation performs real state mutations (via `contract-call? .v0-market-vault debt-remove-scaled` / `collateral-remove`) and only *afterward* hits a failing guard (`ERR-BAD-DEBT-SOCIALIZATION-FAILED`). The already-committed sub-call effects for that position are not undone, while the caller only sees a discarded error inside the response list.

### Finding Description
Inside `liquidate` (mainnet/contracts/market/v0-4-market.clar:1496-1585):
1. `(try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))` executes.
2. `(try! (contract-call? .v0-market-vault debt-remove-scaled borrower scaled-to-remove debt-aid))` — mutates borrower debt bookkeeping in `v0-market-vault`.
3. `(try! (contract-call? .v0-market-vault collateral-remove borrower coll-final collateral-ft coll-aid actual-receiver))` — removes and transfers collateral out.
4. Only *after* these two committed cross-contract calls does the function evaluate bad-debt socialization: [1](#0-0) 
5. If `socialize-debt-asset` fails for any remaining debt asset, `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` returns an `err`. [2](#0-1) 

When `liquidate` is called directly (top-level transaction), returning `err` causes the Stacks VM to roll back the entire transaction, including the two `.v0-market-vault` contract-calls that already succeeded — so no value is stranded.

However, `liquidate-multi` invokes each position through `call-liquidate` inside a `map`, and explicitly discards individual outcomes: [3](#0-2) 

Since the top-level transaction always resolves to `(ok ...)`, the transaction-level rollback that would normally undo `liquidate`'s already-successful `debt-remove-scaled`/`collateral-remove` sub-calls never fires. Those sub-calls are independent atomic units in Clarity (each `contract-call?` commits or rolls back on its own outcome); once they return `ok`, their writes persist for the remainder of the transaction regardless of a later failure in the *calling* function, unless the outer transaction itself ultimately errors.

Net effect: for a borrower whose bad-debt socialization legitimately fails (e.g., `filter-out-debt-asset`/`socialize-debt-asset` encountering an inconsistent egroup or overflow), the batch caller still observes an `err` entry in the returned list for that position, yet the borrower's debt has already been reduced (`debt-remove-scaled`) and their collateral already been seized and sent to the liquidator/receiver (`collateral-remove`). This is exactly the "fold absorbs failure" pattern: mutations are evaluated, and only the fold's discarded per-item result reflects the guard that should have prevented them.

### Impact Explanation
This causes incorrect and unrecoverable protocol accounting: the borrower loses collateral and has debt removed from tracking without the compensating bad-debt socialization/repayment actually completing consistently. This can be leveraged to seize collateral from positions while corrupting the debt ledger (protocol insolvency), or at minimum causes permanent loss/misallocation of a borrower's collateral with no on-chain revert to alert integrators that the "successful-looking" transaction actually contains a failed sub-operation. This lands on the in-scope impact class of protocol insolvency / permanent freezing of funds, since collateral leaves the system while the debt state it was meant to offset is left inconsistent.

### Likelihood Explanation
Likelihood depends on the bad-debt-socialization path actually failing for a position batched via `liquidate-multi` (e.g., an egroup/state edge case causing `socialize-debt-asset` to fail) while other positions in the same call succeed. `liquidate-multi` is a normal public entrypoint reachable by any liquidator bot, and batch liquidation during volatile markets (when socialization edge cases are most likely) is the exact scenario this function is designed for, making the interleaving realistic rather than purely theoretical.

### Recommendation
Do not unconditionally wrap `map call-liquidate positions` in `ok`. Either:
- Propagate/abort the whole batch transaction when any position fails bad-debt socialization (use `try!`/`asserts!` on each result, forcing full transaction rollback), or
- Restructure `liquidate` so that debt/collateral removal (`debt-remove-scaled`, `collateral-remove`) only executes *after* the bad-debt socialization outcome is known to succeed, so a late failure cannot leave partial mutations committed while the surrounding batch call still reports overall success.

### Proof of Concept
1. Liquidator calls `liquidate-multi` with two positions: position A (healthy socialization) and position B (borrower state that will make `socialize-debt-asset` return `false` for at least one asset, e.g., an edge case in `filter-out-debt-asset`/egroup lookups).
2. For position B, `call-liquidate` → `liquidate` executes `vault-system-repay`, then `contract-call? .v0-market-vault debt-remove-scaled` (commits), then `contract-call? .v0-market-vault collateral-remove` (commits, transferring collateral to `actual-receiver`).
3. `socialize-debt-asset`/`fold` fails for position B → `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` returns `err` from `liquidate` for position B.
4. `liquidate-multi`'s `(ok (map call-liquidate positions))` returns `(ok (list (ok {...}) (err u...)))` — the whole transaction succeeds.
5. Result: position B's collateral has already been removed/transferred and its debt-scaled entry already reduced in `v0-market-vault`, even though the caller's own result list shows an `err` for that position and no compensating repayment/socialization actually occurred. [4](#0-3) [5](#0-4) [3](#0-2)

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L1496-1513)
```text
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
