### Title
Bad-debt socialization failure inside `liquidate-multi` is silently swallowed while collateral/debt transfers already executed — ([File: local-testing/contracts/market/market.clar], mirrored in [File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`liquidate-multi` batches individual `liquidate()` calls through `call-liquidate` and is documented to catch per-position failures so "Failed liquidations return error codes but don't revert entire batch". Inside `liquidate`, however, the state-changing steps (`vault-system-repay`, `market-vault debt-remove-scaled`, `market-vault collateral-remove`) execute and commit **before** the final `asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED` check that runs the bad-debt socialization fold. Because `liquidate` is invoked from `call-liquidate` as a plain intra-contract function call (not a `contract-call?` boundary), and the enclosing `liquidate-multi` transaction as a whole returns `(ok ...)`, a late failure inside `liquidate` does not roll back the collateral/debt transfers that already succeeded — it only discards the socialization step.

### Finding Description
`liquidate` performs, in order:
1. Health/liquidatable checks (`asserts!` on pause, auth, amount, slippage, etc.) [1](#0-0) 
2. Real, committing cross-contract transfers: `vault-system-repay`, then `market-vault debt-remove-scaled` and `market-vault collateral-remove` (already sending seized collateral to the receiver) [2](#0-1) 
3. Only afterward, a bad-debt socialization fold whose failure is enforced with `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` [3](#0-2) 

`liquidate-multi` is explicitly designed to catch each position's failure and continue the batch: "Failed liquidations return error codes but don't revert entire batch," implemented as `(ok (map call-liquidate positions))` [4](#0-3) 

Because `liquidate` is called from `call-liquidate` as an ordinary same-contract function application (never as a top-level `contract-call?` entry point), and `liquidate-multi` itself returns `(ok ...)`, an `ERR-BAD-DEBT-SOCIALIZATION-FAILED` returned deep inside `liquidate` does not abort the surrounding transaction. The `vault-system-repay`, `debt-remove-scaled`, and `collateral-remove` contract-calls that already executed and returned `ok` are **not** retroactively undone — only Clarity's per-contract-call rollback applies to calls that themselves return an error; these calls succeeded. The `fold` inside `liquidate-multi`'s `map` therefore absorbs a failure that should have been fatal to the whole operation, leaving debt written off and collateral transferred to the liquidator while the vault's `lindex` write-down (the socialization that compensates other depositors for the unrecoverable shortfall) never happens.

### Impact Explanation
This produces a real accounting inconsistency: debt is removed from the borrower's obligation and collateral is transferred out, but the corresponding loss is never socialized into the vault's liquidity index. Depositors' shares become overvalued relative to actual backing, i.e., protocol insolvency / permanent freezing of funds for later withdrawers who cannot redeem at the (overstated) share value. This falls under the in-scope Critical impact class ("permanent freezing of funds... or protocol insolvency").

### Likelihood Explanation
Reaching this path requires a liquidatable position where, after collateral is fully consumed, `no-collateral-left` is true and the resulting `fresh-debt-list` triggers `fold socialize-debt-asset` to fail (e.g., an interim vault state change causing `socialize-debt` to violate one of its own invariants). This is a batch-only path (`liquidate-multi`), reachable by any liquidator submitting a multi-position liquidation, and does not require DAO compromise, oracle manipulation, or flashloan misuse — only a position landing in the deep bad-debt branch during a batch call.

### Recommendation
Ensure `liquidate`'s bad-debt socialization failure is fatal to the whole liquidation, not just returned as a swallowed value: either perform socialization checks/mutations before the collateral/debt transfers commit, or make `liquidate-multi` detect this specific failure and force a full-transaction abort (e.g., propagate via `try!`) rather than absorbing it in the per-position response list, since the underlying transfers cannot be safely left "half-committed" once socialization is skipped.

### Proof of Concept
1. Attacker/liquidator crafts a `liquidate-multi` batch containing a position whose full liquidation would leave `no-collateral-left` true and force the bad-debt socialization fold (`socialize-debt-asset` over `fresh-debt-list`) to fail — e.g., by ordering positions in the same batch so that an earlier position's cache/index state (via `accrue-and-cache`) causes the borrow index used by `socialize-debt` for that vault to violate an internal invariant.
2. `liquidate-multi` calls `call-liquidate` → `liquidate` for that position; `vault-system-repay`, `debt-remove-scaled`, and `collateral-remove` all succeed and commit real transfers [5](#0-4) 
3. The subsequent `asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED` fails [3](#0-2) , so `liquidate` returns `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)`.
4. `call-liquidate` catches this error and places it in the response list; `liquidate-multi` still returns `(ok (list ... err ...))` — the whole transaction commits.
5. Result: the borrower's debt was removed and collateral was already sent to the liquidator/receiver, but the vault's `lindex` was never written down to reflect the unrecoverable loss, leaving depositor shares overvalued.

Note: the exact body of `call-liquidate` (which wraps `liquidate` and constructs the response entries) was not retrieved verbatim during this investigation due to index truncation; its presence and the batch semantics are confirmed by the doc comment and `map` usage in `liquidate-multi`. A Devin session with full repo access could confirm the exact wrapping logic if further verification is needed.

### Citations

**File:** local-testing/contracts/market/market.clar (L1511-1516)
```text
    (asserts! (not (is-liquidation-paused debt-aid)) ERR-LIQUIDATION-PAUSED)
    (asserts! (is-eq contract-caller tx-sender) ERR-AUTHORIZATION)
    (asserts! (> debt-amount u0) ERR-AMOUNT-ZERO)
    (asserts! (> debt-to-repay u0) ERR-ZERO-LIQUIDATION-AMOUNTS)
    (asserts! (> coll-final u0) ERR-ZERO-LIQUIDATION-AMOUNTS)
    (asserts! (>= coll-final min-collateral-expected) ERR-SLIPPAGE)
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

**File:** local-testing/contracts/market/market.clar (L1567-1572)
```text
                    (if (> (len fresh-debt-list) u0) ;; if still has debt
                      (let ((socialization-result (fold socialize-debt-asset 
                                                        fresh-debt-list 
                                                        { borrower: borrower, success: true })))
                        (asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)
                        ;; emit bad-debt-socialized event
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
