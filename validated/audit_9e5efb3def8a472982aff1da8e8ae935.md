### Title
Bad-debt socialization failure inside `liquidate-multi` batch is absorbed by an error-swallowing `map`, permanently stranding unbacked debt after collateral is already seized - ([File: local-testing/contracts/market/market.clar])

### Summary
The external report's bug class — an unbounded/critical loop whose partial failure is not surfaced to the caller — maps to Zest's bad-debt socialization fold. `socialize-debt-asset` is a `fold` accumulator that silently absorbs mid-loop failures via `(if (not (get success acc)) acc ...)`, and the batched entry point `liquidate-multi` wraps each `liquidate` call in a plain `map` with no `try!`/`asserts!` propagation, so an inner `liquidate` that fails at the very last step (bad-debt socialization) does not abort the transaction — leaving the earlier, already-committed collateral seizure and debt-repayment state changes in place while the debt write-down is skipped.

### Finding Description
`liquidate` performs several sequential, state-mutating `contract-call?`s before it ever validates that bad-debt socialization will succeed:

1. `(try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))` — repays debt in the vault.
2. `(contract-call? .market-vault debt-remove-scaled ...)` — reduces the borrower's obligation.
3. `(contract-call? .market-vault collateral-remove ...)` — seizes and transfers collateral to the liquidator. [1](#0-0) 

Only afterward, if `no-collateral-left` is true, does the function attempt to write off any *remaining* debt via a `fold` over `socialize-debt-asset`: [2](#0-1) 

`socialize-debt-asset` itself is a fold that **absorbs failure** rather than reverting: if any iteration's `unwrap!` on `vault-socialize-debt`, `vault-accrue`, or `debt-remove-scaled` fails, that iteration returns `failed-status`, and every subsequent fold iteration short-circuits via `(if (not (get success acc)) acc ...)` without doing any work — the loop absorbs the failure and just carries the flag to the end: [3](#0-2) 

`liquidate` then checks the aggregated flag with `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` at line 1571 — after steps 1–3 above have already executed and their `contract-call?`s to `.market-vault` and `.vault-*` have already committed.

When `liquidate` is invoked directly by a user, this `asserts!` failing does cause the whole top-level transaction to revert, rolling back steps 1–3 as well — so in the single-call path the bug is masked.

The batched entry point breaks this safety net: [4](#0-3) 

`liquidate-multi` calls `call-liquidate` (which just forwards to `liquidate`) through a plain `map`, with **no `try!` on each individual result**, and finally wraps the entire list in `(ok (map call-liquidate positions))`. Because each per-position `liquidate` invocation is an ordinary same-contract function call (not gated by an outer `try!`/`asserts!`), an `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)` returned by one position's `liquidate` is merely collected as a list element — it never causes `liquidate-multi`'s own return value to become `err`. Since the overall transaction's final value is `(ok ...)`, the transaction commits, and every state change made earlier inside that specific failing `liquidate` call (vault repay, `debt-remove-scaled`, `collateral-remove`) remains permanently committed, even though the function's own logical result was "failed."

This is the same *shape* as the external report's bug (an unguarded loop/aggregate operation whose internal failure does not block downstream, damaging execution) but manifests here as a fold that absorbs failure feeding into a batch entry point that strands value on abort — both explicitly listed analog mechanisms.

### Impact Explanation
For the affected borrower position within a `liquidate-multi` batch: collateral has already been fully removed and transferred to the liquidator, and the liquidated debt asset's scaled debt has already been reduced — but any *other* remaining debt assets that should have been written off via bad-debt socialization are left on the books, now permanently unbacked by any collateral (since `no-collateral-left` was true). This debt can never be liquidated again (no collateral to seize) and can never be repaid by socialization again through the normal liquidation path for that borrower, since the position has no collateral to trigger a fresh liquidation. This constitutes permanent freezing/orphaning of protocol-tracked debt and directly threatens protocol solvency accounting, since the vault-side interest-bearing debt is never written down to reflect the loss, corrupting the liquidity-index-based accounting used to price ztoken collateral (`resolve-ztoken`) for all other users.

### Likelihood Explanation
`liquidate-multi` is presented in the codebase specifically to allow liquidators to batch positions and to prevent "front-running attacks that prevent bad debt socialization," so it is expected to be used regularly by liquidator bots. Any transient failure in `vault-socialize-debt`, `vault-accrue`, or `debt-remove-scaled` for one debt asset in a multi-asset position (e.g., a vault-level pause flipped mid-block, an index-cache race, or a debt-asset-specific edge case) during a batched liquidation is sufficient to trigger this — no attacker collusion or malicious governance action is required, only an operational hiccup on one asset while a batch liquidation is in flight.

### Recommendation
In `liquidate-multi`, replace the unguarded `map` over `call-liquidate` with logic that either (a) requires each `liquidate` result to be explicitly checked so a bad-debt-socialization failure is fed back and the collateral/debt mutations for that position are rejected atomically (e.g., restructure `liquidate` so the socialization fold runs and is validated *before* any vault-mutating `try!`/`contract-call?`s execute), or (b) make `socialize-debt-asset`'s fold fail loudly (propagate the error out of `liquidate` via `try!` before any collateral transfer happens) so partial socialization can never be committed independently of the collateral seizure it's supposed to reconcile.

### Proof of Concept
1. Liquidator calls `liquidate-multi` with a batch including borrower B who holds debt in two assets (e.g., USDC and USDH) and collateral fully consumed by the primary liquidated asset.
2. Inside the nested `liquidate(B, ...)` call: `vault-system-repay`, `market-vault debt-remove-scaled`, and `market-vault collateral-remove` execute and commit (collateral is transferred to the liquidator).
3. `no-collateral-left` evaluates true; the code enters the bad-debt-socialization branch and folds `socialize-debt-asset` over B's remaining debt list (e.g., USDH).
4. The `vault-socialize-debt` (or `vault-accrue`/`debt-remove-scaled`) call for USDH fails (e.g., USDH vault temporarily paused for `accrue`/socialization by an unrelated DAO action, or any transient error path) — the fold returns `{ success: false }`.
5. `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` fails, so this specific `liquidate(B, ...)` call returns `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)`.
6. Back in `liquidate-multi`, this `err` is just the corresponding element of the `map`'d list; `(ok (map call-liquidate positions))` still returns `ok`, so the whole transaction commits.
7. Result: borrower B's collateral is gone and their liquidated-asset debt is reduced, but their USDH debt remains fully on the books with zero collateral — permanently orphaned, unliquidatable, and unsocialized.

### Citations

**File:** local-testing/contracts/market/market.clar (L901-925)
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
            (unwrap! (contract-call? .market-vault
                                      debt-remove-scaled
                                      borrower
                                      scaled-debt
                                      asset-id) failed-status)
          acc)
        ))
```

**File:** local-testing/contracts/market/market.clar (L1518-1535)
```text
    ;; execute liquidation
    (try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))

    ;; update obligations and socialize bad debt
    (let ((debt-updated (try! (contract-call? .market-vault
                              debt-remove-scaled
                              borrower
                              scaled-to-remove
                              debt-aid)))
          ;; Collateral receiver defaults to liquidator if not specified
          (actual-receiver (match collateral-receiver recv recv liquidator))
          (coll-removed (try! (contract-call? .market-vault
                              collateral-remove
                              borrower
                              coll-final
                              collateral-ft
                              coll-aid
                              actual-receiver)))
```

**File:** local-testing/contracts/market/market.clar (L1558-1571)
```text
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

**File:** local-testing/contracts/market/market.clar (L1610-1622)
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
