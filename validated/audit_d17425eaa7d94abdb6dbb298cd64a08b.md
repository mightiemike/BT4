## Title
`liquidate-multi` allows a `fold`/`map`-absorbed failure inside `liquidate` to commit partial state mutations (repay + collateral seizure) while later bad-debt socialization silently fails — (File: `mainnet/contracts/market/v0-4-market.clar`)

## Summary
`liquidate-multi` batches independent `liquidate()` calls via `map`, wrapping the result unconditionally in `(ok ...)` so that a failing position "returns an error code but doesn't revert the batch." [1](#0-0)  This mirrors the Asymmetry `Reth.withdraw` bug class of "a fold that absorbs failure": individual failures are swallowed, but because Clarity only rolls back state at the *outermost* public-function boundary, mutations that already executed inside a `liquidate()` call before it internally fails are **not** undone when `liquidate-multi` still returns `ok` overall.

## Finding Description
`liquidate` performs its `asserts!` guards strictly before any state mutation, then executes mutations in sequence: `vault-system-repay` (real token transfer + vault-level debt reduction), `debt-remove-scaled`, `collateral-remove`, and — only when the position is fully seized — a `fold` over `socialize-debt-asset` guarded by a final `asserts!`. [2](#0-1) 

`socialize-debt-asset` itself is a fold that "absorbs" a failure of any one asset in `fresh-debt-list` via `unwrap!`, short-circuiting only the fold's own accumulator, not the transaction: [3](#0-2) 

When called directly, if the final `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` fails, `liquidate` returns `(err ...)` as the **top-level** transaction result, so the Stacks VM discards every mutation made earlier in the same call (`vault-system-repay`, `debt-remove-scaled`, `collateral-remove`, and any partially-succeeded socialization entries) — this is safe.

However, when invoked through `liquidate-multi`, `call-liquidate` calls `liquidate` as a plain (non-`contract-call?`, non-`try!`) function inside `map`: [4](#0-3)  and the batch entry point wraps everything in `(ok (map call-liquidate positions))` regardless of any individual `err`: [5](#0-4) 

Because the outermost public function (`liquidate-multi`) returns `ok`, the VM commits the entire accumulated working state for the transaction — including the `vault-system-repay`, `debt-remove-scaled`, and `collateral-remove` mutations, plus whichever prefix of `socialize-debt-asset` fold entries succeeded before the failing entry — even though that specific position's `liquidate()` call ultimately returned `err`.

## Impact Explanation
This breaks the atomicity invariant that a single `liquidate()` call is checked in `market.clar`'s design comments and by direct-call semantics: "collateral removed + debt repaid + bad debt socialized" is supposed to be all-or-nothing. Via `liquidate-multi`, a position whose final bad-debt-socialization step fails (e.g., one asset in `fresh-debt-list` fails `vault-socialize-debt` or the nested `debt-remove-scaled` call) will still have:
- the liquidator's real repayment tokens consumed by the vault (`vault-system-repay`),
- the borrower's collateral fully seized and sent to the liquidator (`collateral-remove`),
- the primary debt asset removed from the borrower's ledger,

while the leftover debt asset that needed socialization is left un-written-off on a borrower who now has **zero collateral** and no way to ever repay it. This corrupts the market-vault's aggregate-debt vs. per-account-debt invariant (bad debt that should have been socialized into vault reserves remains permanently unaccounted), which is a protocol-insolvency-class defect (Critical) — real assets left the system (to the liquidator) and the vault's book-keeping for the corresponding debt no longer matches reality, permanently freezing/misstating funds tied to that leftover debt entry.

## Likelihood Explanation
`liquidate-multi` is a public, permissionless entry point intended precisely for batch liquidation; the code comment confirms the "isolate failures per position" design intent is assumed safe, but the underlying Clarity execution model (per-function early-return via `asserts!`/`unwrap!`, only top-level `err` triggers rollback) makes that assumption incorrect whenever a later step can fail after an earlier mutation. The socialization fold is a realistic later-failure point since it depends on nested nested `contract-call?`s (`vault-socialize-debt`, `debt-remove-scaled`) that can fail for reasons independent of the earlier repay/seizure steps (e.g., vault-side caps, arithmetic edge cases in scaled amounts, or partial socialization across multiple assets in `fresh-debt-list`).

## Recommendation
In `call-liquidate`, wrap the inner `liquidate` call so any `err` propagates to abort the whole `liquidate-multi` transaction for that position's effects, or restructure `liquidate-multi` to invoke each `liquidate` via `contract-call?` to itself (or an isolated sub-contract) so each position's mutations are transactionally isolated and only committed when that specific `liquidate` call succeeds — rather than relying on `map` + unconditional `(ok ...)` to "isolate" per-position failures.

## Proof of Concept
1. Attacker/liquidator identifies a borrower position that is liquidatable and, once seized, leaves `no-collateral-left = true` with multiple remaining debt assets in `fresh-debt-list`. [6](#0-5) 
2. Craft/observe conditions such that the second (or later) entry processed by `fold socialize-debt-asset` fails inside `vault-socialize-debt` or the nested `debt-remove-scaled` contract-call (e.g., vault-side reserve/cap check, or scaled-debt edge case). [7](#0-6) 
3. Call `liquidate-multi` with this position included (alone or with other valid positions). [1](#0-0) 
4. Inside `call-liquidate` → `liquidate`, the earlier `vault-system-repay`, `debt-remove-scaled`, and `collateral-remove` calls execute and mutate state; the fold's final `asserts!` on `socialization-result` then fails, and `liquidate` returns `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)` to `call-liquidate`.
5. `liquidate-multi` still returns `(ok (list ... (err ERR-BAD-DEBT-SOCIALIZATION-FAILED) ...))` — a successful top-level transaction — so the VM commits all prior mutations from step 4 instead of rolling them back, unlike what would happen calling `liquidate` for the same borrower directly (which would revert atomically).

## Uncertainty
I was not able to fully confirm from the indexed snippets under what precise real-world conditions `vault-socialize-debt` or the nested `debt-remove-scaled` inside `socialize-debt-asset` can independently fail (their full bodies in `market-vault.clar` / `vault-*.clar` were not retrieved in this session), so the exact trigger for the later-step failure is inferred from the guard/mutation ordering rather than directly observed. A Devin session with full repository access would be needed to enumerate concrete failure conditions in `vault-socialize-debt` and `debt-remove-scaled` to fully validate exploitability.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1488-1548)
```text
    (asserts! (not (is-liquidation-paused debt-aid)) ERR-LIQUIDATION-PAUSED)
    (asserts! (is-eq contract-caller tx-sender) ERR-AUTHORIZATION)
    (asserts! (> debt-amount u0) ERR-AMOUNT-ZERO)
    (asserts! (> debt-to-repay u0) ERR-ZERO-LIQUIDATION-AMOUNTS)
    (asserts! (> coll-final u0) ERR-ZERO-LIQUIDATION-AMOUNTS)
    (asserts! (>= coll-final min-collateral-expected) ERR-SLIPPAGE)

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
