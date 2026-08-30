### Title
`liquidate-multi` swallows a failed `liquidate()` sub-call inside `(ok (map ...))`, letting partial state changes (repay + collateral seizure) commit while bad-debt socialization is skipped - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`liquidate-multi` executes each batch item via `(map call-liquidate positions)` and unconditionally wraps the entire result list in `(ok ...)`. Because the wrapping public function always returns `(ok ...)`, the Stacks VM commits **all** state mutations from the transaction — even those produced by an individual `liquidate()` invocation inside the map that itself terminated early with `(err ...)`. This is the Clarity analog of the reported Solidity bug: instead of causing the whole transaction to `revert` when a sub-call fails, the failure is captured and "returned" as a value in a list, and everything that already executed inside that failed sub-call stays persisted.

### Finding Description
`liquidate()` performs several sequential, individually-committing steps: [1](#0-0) 

1. Asserts (pause/auth/amount/slippage) run first.
2. `(try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))` performs a real `contract-call?` into the vault: it pulls the debt token from the liquidator and reduces `total-borrowed`/`principal-scaled` in that vault — this commits as soon as that sub-call itself returns `ok`.
3. `debt-remove-scaled` and `collateral-remove` are then invoked via `try!` against `.v0-market-vault`, actually transferring seized collateral to the receiver.
4. Only if `no-collateral-left` is true does the code attempt to socialize any remaining "other" debt via `fold socialize-debt-asset ...`, followed by: [2](#0-1) 

`socialize-debt-asset` is a fold that "absorbs" failure into an accumulator flag rather than reverting the already-applied vault mutations from earlier fold iterations: [3](#0-2) 

If the fold produces `success: false` for any reason (a paused vault operation, a zero-amount edge case, etc. in one of the three internal `unwrap!` calls), the outer `asserts!` fires with `ERR-BAD-DEBT-SOCIALIZATION-FAILED`, making the whole `liquidate()` call return `(err ...)` — but only *after* `vault-system-repay`, `debt-remove-scaled`, and `collateral-remove` have already committed real token transfers and accounting updates.

When `liquidate()` is called directly as the top-level entry point, this `(err ...)` return causes the Stacks VM to roll back the entire transaction, so the earlier committed steps are undone too — this is the safe path.

However, `liquidate-multi` invokes `liquidate()` only indirectly, through `call-liquidate` inside `map`, and then wraps the whole list in `(ok ...)`: [4](#0-3) [5](#0-4) 

Because the top-level function `liquidate-multi` ultimately evaluates to `(ok (list ... (err ERR-BAD-DEBT-SOCIALIZATION-FAILED) ...))`, the transaction as a whole succeeds. Clarity's rollback-on-error semantics are keyed to the outermost call's final response, not to intermediate function calls invoked in-contract (as opposed to `contract-call?` boundaries, which only roll back their own call). The net effect: the borrower ends up with **zero collateral but unsocialized remaining debt** — an inconsistent, unbacked obligation that was never supposed to exist outside of an atomic "seize everything + socialize everything" operation.

### Impact Explanation
The borrower's position now has no collateral while retaining debt in one or more other assets that was never written down through `socialize-debt-asset`. That debt is permanently unbacked and cannot be liquidated again (there is no collateral left to seize), so it can never be economically recovered from the borrower. The corresponding vault(s) supplying that debt asset have lent funds that will never be repaid or socialized, which is a permanent freezing/loss of the associated lenders' deposited principal and interest — a protocol insolvency-class outcome restricted to the batch-liquidation code path.

### Likelihood Explanation
This requires only a single transaction: any address can call `liquidate-multi` with a batch that includes a position whose bad-debt socialization step can be made to fail (e.g., by including many debt assets so a downstream `unwrap!` inside `socialize-debt-asset` fails, or by racing a legitimate transient vault-level failure such as a stale-index/pause condition on one of the "other debt" vaults). No governance/DAO compromise, oracle manipulation, or multi-user interference is needed — a single caller with a single crafted batch is sufficient.

### Recommendation
Do not swallow individual `liquidate()` failures inside `liquidate-multi`. Either:
- Explicitly detect an `(err ...)` result from `call-liquidate` and `(try! ...)`/`asserts!` on it so the whole `liquidate-multi` transaction reverts on any single failure, or
- Redesign `socialize-debt-asset`/`liquidate()` so that any bad-debt-socialization failure itself unwinds (via `try!`/`asserts!` propagated to the top level) the collateral-remove/debt-remove/vault-system-repay steps already performed for that position, guaranteeing the whole per-position liquidation is atomic regardless of whether it is invoked standalone or via the batch entry point.

### Proof of Concept
1. Liquidator calls `liquidate-multi` with a batch containing one position `P` where the borrower has multiple outstanding debt assets and the collateral being seized is exactly `user-coll-balance` (so `no-collateral-left` evaluates true).
2. Inside `call-liquidate` → `liquidate(P)`:
   - `vault-system-repay` executes and commits (liquidator's tokens pulled, vault's `total-borrowed` reduced).
   - `debt-remove-scaled` and `collateral-remove` execute and commit (borrower's primary debt reduced, collateral transferred to liquidator).
   - `fold socialize-debt-asset` runs over the borrower's remaining debts; one entry's internal `unwrap!` fails (e.g., a vault-level pause/edge-case on that specific asset), setting `success: false` for the accumulator.
   - `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` fires, so `liquidate(P)` itself returns `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)`.
3. `map` captures this `(err ...)` as one element of the results list; `liquidate-multi` still returns `(ok (list ... (err ERR-BAD-DEBT-SOCIALIZATION-FAILED) ...))`.
4. Because the outermost call succeeds, the transaction commits: the borrower now has zero collateral and unsocialized debt on the vault(s) whose socialization step failed — permanently unbacked debt that the protocol can never recover, and lenders in that vault lose their principal.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1511-1535)
```text
                              coll-aid
                              actual-receiver)))

          (target-coll-full-usd (normalize (* user-coll-balance coll-price) coll-decimals false))
          (other-coll-usd (if (> total-collateral-usd target-coll-full-usd)
                              (- total-collateral-usd target-coll-full-usd)
                              u0))
          (other-debt-repayable
            (if (> other-coll-usd u0)
              (let ((other-adj (div-bps-down other-coll-usd (+ BPS liq-penalty-max)))
                    (other-tokens (mul-div-down other-adj (pow u10 debt-decimals) debt-price))
                    (other-borrow-idx (get index (unwrap-panic (get-cached-indexes debt-aid))))
                    (other-scaled (mul-div-down other-tokens INDEX-PRECISION other-borrow-idx)))
                (mul-div-up other-scaled other-borrow-idx INDEX-PRECISION))
              u0))
          (no-collateral-left (and
                                (is-eq coll-removed u0)
                                (or
                                  (is-eq (len (get collateral pos-full)) u1)
                                  (and
                                    (is-eq (len (get collateral pos-full)) (len (get collateral position)))
                                    (is-eq other-debt-repayable u0))))))

      ;; Handle bad debt socialization if no collateral left
      (let ((bad-debt-socialized 
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1544-1548)
```text
                    (if (> (len fresh-debt-list) u0) ;; if still has debt
                      (let ((socialization-result (fold socialize-debt-asset 
                                                        fresh-debt-list 
                                                        { borrower: borrower, success: true })))
                        (asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1592-1599)
```text
;; Failed liquidations return error codes but don't revert entire batch
(define-public (liquidate-multi
                (positions (list 64 { borrower: principal,
                                      collateral-ft: <ft-trait>,
                                      debt-ft: <ft-trait>,
                                      debt-amount: uint,
                                      min-collateral-expected: uint })))
  (ok (map call-liquidate positions)))
```
