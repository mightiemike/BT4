### Title
Failed liquidation inside `liquidate-multi` leaves seized collateral and reduced debt permanently applied - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`liquidate` performs its critical state mutations (`vault-system-repay`, `debt-remove-scaled`, `collateral-remove`) *before* the final bad-debt-socialization check that can still cause the whole function to return an error. When `liquidate` is invoked through `liquidate-multi` (which calls it via the internal `call-liquidate` helper without `try!`), a later failure inside the same `liquidate` execution does not roll back the already-executed, successful `contract-call?`s to `.v0-market-vault`, because each of those cross-contract calls is its own atomic sub-transaction that already committed on success. `liquidate-multi` swallows the resulting `(err ...)` into its output list and returns `(ok (list ...))` for the whole transaction, so the transaction as a whole succeeds even though one of the batched liquidations logically "failed."

### Finding Description
In `liquidate` (`mainnet/contracts/market/v0-4-market.clar`), the sequence is:
1. `(try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))` [1](#0-0) 
2. `(debt-updated (try! (contract-call? .v0-market-vault debt-remove-scaled borrower scaled-to-remove debt-aid)))` and `(coll-removed (try! (contract-call? .v0-market-vault collateral-remove borrower coll-final collateral-ft coll-aid actual-receiver)))` — both already-committed sub-transactions to a separate contract [2](#0-1) 
3. Only afterward, if `no-collateral-left` is true, the function attempts bad-debt socialization via `(fold socialize-debt-asset fresh-debt-list ...)` and then `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` [3](#0-2) 

If that final `asserts!` fails, `liquidate` returns `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)`. Because steps (1) and (2) were each individual, already-succeeded `contract-call?`s to `.v0-market-vault`, their state changes (debt reduced, collateral seized and sent to the liquidator) are not automatically undone by a later, unrelated failure occurring purely within `liquidate`'s own (non-`contract-call?`) execution frame.

`liquidate-multi` calls `liquidate` indirectly through `call-liquidate`, which invokes `liquidate` directly (not via `try!`/`contract-call?`), and simply returns whatever response it produces as one element of a `map`: `(ok (map call-liquidate positions))` [4](#0-3) . This matches the "fold/map that absorbs failure" and "multi-step entry point that strands value on abort" analog classes: the top-level transaction (`liquidate-multi`) commits successfully overall, while one position's `liquidate` sub-call reports failure yet has already durably mutated `.v0-market-vault` state via its own successful `contract-call?`s.

### Impact Explanation
For the affected borrower, collateral has already been transferred out via the committed `collateral-remove` call and scaled debt already reduced via `debt-remove-scaled`, but the batch caller (`liquidate-multi`) records this position as an error. This can strand or destroy value for the borrower/protocol: debt accounting and collateral custody diverge from what "success/failure" reporting implies, and downstream consumers relying on `liquidate-multi`'s per-position `(response ...)` semantics to determine whether state changed will be misled — a case of temporary/permanent freezing of funds and incorrect debt/collateral bookkeeping for the affected account, satisfying the "temporary freezing of funds" impact class.

### Likelihood Explanation
Reachable by any liquidator submitting a single-transaction `liquidate-multi` call containing a position that reaches `no-collateral-left` where the subsequent bad-debt socialization step fails (e.g., `vault-socialize-debt` or `debt-remove-scaled` inside `socialize-debt-asset` returning an error for that specific debt asset). No privileged access or DAO action is required — only crafting position parameters (or ordinary market conditions) that make the final socialization assert fail after the earlier mutations succeeded.

### Recommendation
Wrap the call to `liquidate` inside `call-liquidate`/`liquidate-multi` such that any error return from `liquidate` is guaranteed to roll back all of its nested `contract-call?` mutations, e.g. by ensuring `liquidate` itself is only ever invoked via a genuine `contract-call?` boundary (so VM-level atomic rollback applies to the whole `liquidate` invocation), or by re-ordering `liquidate` so that all fallible checks (including bad-debt socialization) are validated/simulated *before* any mutating `contract-call?`s to `.v0-market-vault` are executed.

### Proof of Concept
1. Liquidator calls `liquidate-multi` with a list containing one position for `borrower` whose position, after seizing all available collateral in this call, leaves `no-collateral-left = true` and non-zero remaining debt in `fresh-debt-list`.
2. Inside `liquidate`, `vault-system-repay`, `debt-remove-scaled`, and `collateral-remove` all execute and commit successfully against `.v0-market-vault` [5](#0-4) .
3. `socialize-debt-asset`'s fold hits a failure for one of the remaining debt entries (e.g., `vault-socialize-debt` returns an error for that asset) [6](#0-5) , causing `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` to fail and `liquidate` to return `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)` [3](#0-2) .
4. `call-liquidate` returns this `err` response as-is; `liquidate-multi` wraps the whole list in `(ok ...)` and the transaction commits [7](#0-6) .
5. Result: borrower's collateral is already seized and debt already reduced (from steps 2), yet the batch reports this position as a failure — state has changed despite the reported failure.

Note: I could not fully verify from static inspection alone whether Clarity's VM applies an additional rollback boundary specifically to a `define-public` function invoked via a *direct* same-contract call (as opposed to `contract-call?`); this uncertainty affects whether the described divergence is exploitable exactly as described versus being caught by an undocumented VM-level safeguard. This should be confirmed with actual on-chain/testnet execution.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1544-1548)
```text
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
