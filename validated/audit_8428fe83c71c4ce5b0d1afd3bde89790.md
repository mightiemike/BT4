### Title
`liquidate-multi` swallows per-position failures via `map`, stranding a liquidator's already-transferred debt repayment when a later step in the same `call-liquidate` invocation reverts - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`liquidate-multi` executes each position through `(map call-liquidate positions)` and always returns `(ok ...)` regardless of the individual outcomes [1](#0-0) . `call-liquidate` is a plain (non `contract-call?`) invocation of the public `liquidate` function [2](#0-1) . Inside `liquidate`, the liquidator's debt tokens are pulled and transferred to the vault via `vault-system-repay` *before* the later `collateral-remove` contract-call that can still fail [3](#0-2) . Because the top-level transaction (the actual `liquidate-multi` call) always returns `ok`, Clarity's whole-transaction rollback guarantee never triggers for an individual failing list item - only the failing item's own trailing `contract-call?` is rolled back, not the earlier, already-committed `vault-system-repay` transfer performed by that same `call-liquidate` invocation.

### Finding Description
This is the Clarity analog of the reported `UniProxy.depositSwap` bug: the audited code assumed that a downstream step (`Router.exactInput`) would always revert atomically together with the enclosing operation if something is off, when in fact a pre-transfer/state-changing action executes and is *not* automatically undone by a later failure. In Zest, the equivalent "downstream call assumed to be all-or-nothing" is `liquidate-multi`'s use of `map`:

1. `liquidate-multi` iterates positions with `(map call-liquidate positions)` and wraps the whole list in `(ok ...)`, so the *outer* transaction never returns `err`, no matter how many individual liquidations fail [1](#0-0) .
2. `call-liquidate` calls the public `liquidate` function directly (same-contract call, not a nested `contract-call?`) [2](#0-1) .
3. Inside `liquidate`, all up-front `asserts!` (pause, auth, amount, slippage) pass, and then `(try! (vault-system-repay debt-aid debt-to-repay debt-ft debt-address))` executes, pulling the liquidator's debt-asset tokens and committing them into the vault (a successful nested `contract-call?`, which commits its own state) [4](#0-3) .
4. Only *after* this transfer has committed does the code call `debt-remove-scaled` and `collateral-remove` via further `contract-call?`s [5](#0-4) . If `collateral-remove` fails for that particular position (e.g., the position's tracked collateral was already reduced by an earlier entry in the same batch targeting the same borrower/asset, making the pre-computed `coll-final` stale and the internal `remove-user-collateral` assertion fail), `liquidate` returns `err`.
5. Because `call-liquidate`'s `err` is only recorded inside the `map` result list (not propagated as the transaction's own return value), Clarity's transaction-level rollback never fires. The already-committed `vault-system-repay` transfer for that failed position is **not** undone, even though the corresponding debt-removal/collateral-removal for that same position was rolled back (as it was itself a distinct failing `contract-call?`).

The root cause is the value-bound "debt tokens transferred to the vault" via `vault-system-repay`, which is never invalidated when the later step (`collateral-remove`) for the *same logical liquidation* fails, because the failure is absorbed by `map` in `liquidate-multi` instead of aborting the whole transaction.

### Impact Explanation
A caller of `liquidate-multi` can lose real debt-asset tokens with no compensating collateral and no benefit to the targeted borrower's position for the failed entries in the batch - direct loss of user (liquidator) funds at rest/in motion, matching the Critical impact class (theft/permanent loss of funds other than unclaimed yield). It can also leave the protocol's internal debt/collateral accounting inconsistent between the vault (which received a real repayment) and the market's obligation registry (which may not have been updated for that entry), risking further accounting drift.

### Likelihood Explanation
This requires only a single transaction from a single caller (the liquidator/bot submitting a batch): including two (or more) overlapping entries for the same borrower/asset in one `liquidate-multi` call, or any other batch entry whose collateral-removal step becomes invalid due to state changes made by an earlier entry processed within the same `map` call, is sufficient to trigger loss for that entry while the overall call still succeeds. Liquidation bots that build large multi-position batches from a stale snapshot of on-chain state are realistically exposed to this without malicious intent.

### Recommendation
Do not use `map`/`fold` to silently swallow failures from a multi-step operation that performs value transfers before its final validation. Either:
- Restructure `liquidate` so that the token pull (`vault-system-repay`) happens only after every validation and every state-mutating step for that position has been confirmed to succeed (single atomic sequence with the transfer last), or
- Have `call-liquidate`/`liquidate-multi` explicitly detect a failed entry and refund/undo any resources pulled for that specific entry before continuing to the next position, or
- Avoid batching altogether for entries that can interact with each other's state (e.g., disallow duplicate borrower/asset pairs in the same batch), and re-validate freshly-read collateral/debt amounts for each entry immediately before performing the token pull.

### Proof of Concept
1. Borrower `B` has a liquidatable position with collateral asset `C` and debt asset `D`.
2. Liquidator submits `liquidate-multi` with two entries both targeting `(borrower: B, collateral-ft: C, debt-ft: D, ...)`, where the combined `debt-amount`/`min-collateral-expected` of the two entries together exceed what `B`'s real remaining collateral can satisfy for the second entry.
3. `map call-liquidate positions` executes entry 1: `vault-system-repay` pulls debt tokens from the liquidator and commits; `debt-remove-scaled`/`collateral-remove` succeed, reducing `B`'s remaining collateral.
4. Entry 2 executes with pre-computed `coll-final` based on the (stale) collateral snapshot taken at the start of `liquidate-multi`. `vault-system-repay` again pulls debt tokens from the liquidator and commits.
5. `collateral-remove` for entry 2 fails (`ERR-INSUFFICIENT-COLLATERAL`) because entry 1 already consumed the collateral; `liquidate` returns `err` for entry 2, but only entry 2's own nested `contract-call?`s roll back - the `vault-system-repay` transfer for entry 2 remains committed.
6. `liquidate-multi` still returns `(ok (list (ok ...) (err ...)))`; the liquidator has permanently lost the debt tokens transferred for entry 2 with no collateral or debt-record benefit. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1488-1512)
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
