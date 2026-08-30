### Title
Failed `liquidate` calls inside `liquidate-multi` strand already-executed seizures/debt-writeoffs because the batch wraps every result in `(ok ...)` - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`liquidate-multi` maps `call-liquidate` (which directly invokes the public `liquidate` function) over a batch of positions and unconditionally returns `(ok (map call-liquidate positions))` [1](#0-0) . Because the top-level transaction always returns `ok`, Clarity's transaction-level rollback guarantee never triggers for that transaction, even when one of the individual `liquidate` calls inside the batch reaches a late-stage `asserts!` failure after it has already executed multiple committed sub-mutations (debt repay, debt removal, collateral removal).

### Finding Description
`liquidate` performs its state mutations in a specific order: it first does `vault-system-repay`, then `debt-remove-scaled` and `collateral-remove` via `contract-call?` to `.v0-market-vault` [2](#0-1) . Only *after* these mutations succeed does it compute `no-collateral-left` and, if true, `fold`s over `socialize-debt-asset` to write off the borrower's remaining debt on other assets, followed by `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` [3](#0-2) .

When `liquidate` is invoked directly as a normal function call (not `contract-call?`) from `call-liquidate` inside a `map` in `liquidate-multi`, an `err` returned by that late `asserts!` becomes just a value in the result list; it does **not** cause `liquidate-multi`'s own return value to be `err`. Since `liquidate-multi` always wraps the whole batch as `(ok (map call-liquidate positions))`, the top-level Clarity transaction always evaluates to `ok`. Clarity's guarantee that "the entire transaction rolls back if it returns err" therefore never engages for this transaction, so the vault-repay, debt-removal, and collateral-removal that occurred *before* the socialization failure remain committed — they are not automatically undone just because the socialization fold that ran afterward failed.

This is the structural analog of the referenced Perennial bug: a multi-step entry point performs several successful sub-steps, then hits a later guard/step that fails, but the failure is absorbed by an outer wrapper (`(ok (map ...))` here, vs. the Vault's decision to still call the product with `0` there) instead of aborting the whole operation, stranding value (seized collateral, partially-cleared debt bookkeeping) in an inconsistent state.

### Impact Explanation
For the affected position: collateral has already been transferred to the liquidator/receiver via `collateral-remove`, and the specific debt asset's scaled debt has already been reduced via `debt-remove-scaled` — but if `no-collateral-left` is true and the socialization fold over the borrower's *other* outstanding debts fails (e.g., one of the sub-calls in `socialize-debt-asset` errors), those other debts are never written off. The borrower ends up with zero collateral backing debt that remains fully on the books, unrecoverable through further liquidation (there is no more collateral to seize), which is either permanent freezing of that residual debt's backing (protocol insolvency exposure) or a discrepancy that leaves bad debt permanently unsocialized on-chain. This lands in the in-scope High/Critical impact class of protocol insolvency / permanent freezing of funds tied to this position's leftover debt.

### Likelihood Explanation
This requires: (1) a borrower position where seizing all collateral for one debt asset triggers `no-collateral-left`, (2) additional outstanding debt on other assets requiring socialization, and (3) one of the socialization sub-calls (`vault-socialize-debt`, `vault-accrue`, or `debt-remove-scaled`) failing — plausible under vault-specific pause states or edge-case numeric conditions in the fold. Because `liquidate-multi` is a normal batch-liquidation entry point intended for routine use (front-run protection), the precondition of hitting this edge case doesn't require any privileged access, just a borrower with multi-asset debt and a socialization sub-call failure during the batch's execution — a realistic, reachable path for liquidator bots exercising the batch API.

### Recommendation
Do not swallow individual `liquidate` failures inside `liquidate-multi` when partial state has already been committed as part of the same call. Either (a) make `call-liquidate`/`liquidate` fully self-contained atomic units by having `liquidate-multi` explicitly propagate a hard failure (returning the whole batch as `err`) whenever `no-collateral-left` triggers a failed bad-debt socialization, so the entire transaction — and thus all of that position's mutations — rolls back; or (b) restructure `liquidate` so bad-debt socialization is verified/reserved *before* any of the debt-repay/collateral-removal mutations are executed, ensuring no value-moving step can succeed while a downstream mandatory step is still able to fail.

### Proof of Concept
1. Borrower has debt in two assets (Debt-A, Debt-B) and collateral entirely in one asset (Coll-X).
2. A liquidator calls `liquidate-multi` with one position targeting this borrower on Debt-A/Coll-X, sized to seize all of Coll-X.
3. Inside `call-liquidate` → `liquidate`: `vault-system-repay` (Debt-A) succeeds, `debt-remove-scaled` (Debt-A) succeeds, `collateral-remove` (Coll-X, full amount) succeeds [2](#0-1) .
4. `no-collateral-left` evaluates true (all Coll-X seized, was the only collateral) [4](#0-3) .
5. The `fold socialize-debt-asset` over the remaining Debt-B entry hits a failure inside `socialize-debt-asset` (e.g., `vault-socialize-debt` for Debt-B's vault returns an error due to a vault-specific condition), so `socialization-result` has `success: false` [5](#0-4) .
6. `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` fails, so `liquidate` returns `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)`.
7. `call-liquidate` returns this `err` value as a list entry; `liquidate-multi` still returns `(ok (list (err ...)))` [1](#0-0) .
8. Because the top-level transaction returns `ok`, the transaction commits: Coll-X is gone from the borrower (transferred out), Debt-A bookkeeping was already reduced, but Debt-B remains as outstanding, now-unbacked debt that can never again be liquidated (no collateral left to seize), stranding that debt permanently unresolved on-chain.

Note: this PoC's premise — that a value returned from a directly-invoked `define-public` function inside a `map`/`fold` does not force a transaction-level rollback of earlier successful `contract-call?` mutations unless the top-level return is itself `err` — is a Clarity VM semantics claim I could not fully verify against primary VM documentation within this session (the referenced `sip-033-clarity4.md` file content was not retrievable in the available tool calls). This should be independently confirmed against Clarity's actual atomicity rules (i.e., whether `contract-call?` sub-invocations get their own commit/rollback checkpoint independent of the top-level return) before treating this as a confirmed vulnerability rather than a hypothesis.

### Citations

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1526-1532)
```text
          (no-collateral-left (and
                                (is-eq coll-removed u0)
                                (or
                                  (is-eq (len (get collateral pos-full)) u1)
                                  (and
                                    (is-eq (len (get collateral pos-full)) (len (get collateral position)))
                                    (is-eq other-debt-repayable u0))))))
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1593-1599)
```text
(define-public (liquidate-multi
                (positions (list 64 { borrower: principal,
                                      collateral-ft: <ft-trait>,
                                      debt-ft: <ft-trait>,
                                      debt-amount: uint,
                                      min-collateral-expected: uint })))
  (ok (map call-liquidate positions)))
```
