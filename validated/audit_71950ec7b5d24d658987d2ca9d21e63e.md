Confirmed: `receive-tokens` performs `(contract-call? asset transfer amount account current-contract none)` on an arbitrary `<ft-trait>` implementation, so the asset contract's code fully executes (and can call back into `market-vault`) before `collateral-add` finishes.

### Title
Reentrancy through `<ft-trait>` token callback in `collateral-add` lets an attacker orphan collateral under an unreachable position id - (File: mainnet/contracts/market/v0-market-vault.clar)

### Summary
`collateral-add` derives/creates a user's position id and mutates the collateral map for that id *before* it performs an external call to the caller-supplied `ft` token contract, and only persists the id-to-account mapping (`insert`) *after* that external call returns. Because the token contract executing the transfer is arbitrary code invoked via `contract-call?`, it can re-enter `collateral-add` for the same account before the outer call's `insert` has run, causing a second, distinct position id to be created and credited with collateral that becomes permanently unreachable once the outer call's `insert` overwrites the `reverse` map pointer.

### Finding Description
In `collateral-add`: [1](#0-0) 

The `let` bindings eagerly evaluate `(resolve-or-create account)`, which for a new/at that point unresolved account calls `create`, which calls `increment`: [2](#0-1) 

`resolve-or-create` decides whether to create a new id purely based on whether `reverse` already has an entry for `account`: [3](#0-2) 

That `reverse`/`registry` mapping is only committed by `insert`, which in `collateral-add` runs *after* the external call `receive-tokens`: [4](#0-3) 

`receive-tokens` invokes an arbitrary caller-supplied `<ft-trait>` contract's `transfer` entry point via `contract-call?`, which is fully executable code, not a passive balance check: [5](#0-4) 

Sequence:
1. Attacker calls `collateral-add` for `account` with a malicious `ft` contract implementing `ft-trait`. `resolve-or-create` finds no entry in `reverse`, so `create` runs, calling `increment` and returning a fresh id `N1`. `add-user-collateral` credits `collateral[{id: N1, asset}]` with `amount1` — this map-set happens as part of binding evaluation, before `insert` for id `N1` has run.
2. `try! (receive-tokens ft amount account)` calls into the attacker's `ft` contract's `transfer` function.
3. Inside that callback, the attacker re-enters `collateral-add` for the *same* `account` (same `contract-caller`, since `market-vault`'s `check-impl-auth` only checks that the caller is the registered `impl` — i.e. `market.clar` — and the attacker drives this reentrant call through `market.clar` itself, which has no reentrancy guard on its collateral entrypoint calling into `market-vault`). Because `insert updated-entry` for the first call has not executed yet, `map-get? reverse account` still returns `none`, so `resolve-or-create` again takes the `create` branch, calling `increment` a second time and returning a *new* id `N2` distinct from `N1`. `add-user-collateral` credits `collateral[{id: N2, asset}]`.
4. The reentrant call proceeds to its own `receive-tokens` and then `insert`, writing `registry[N2]` and `reverse[account] = N2`.
5. Control returns to the outer (first) call, which then executes its own `insert`, writing `registry[N1]` and overwriting `reverse[account] = N1`.
6. Final state: `reverse[account]` points only to `N1`. The collateral credited under `N2` (`collateral[{id: N2, asset}]`) is still in the `collateral` map but is permanently unreachable — no `resolve`/`resolve-safe`/`get-position` call for `account` can ever surface id `N2` again, since those all resolve through `reverse`.

### Impact Explanation
Funds credited under the orphaned id `N2` are permanently locked/frozen: they remain recorded in the `collateral` map (so they are not literally "lost" from a total-value-locked perspective, and cannot be withdrawn or used as collateral) but the account can never again reach that id through `resolve`, `resolve-safe`, or `get-position`, and `collateral-remove` also resolves the account via `resolve`, so no future `collateral-remove` call can target `N2`. This is a temporary/permanent freezing of user-supplied collateral funds, matching the in-scope "permanent freezing of funds" impact class.

### Likelihood Explanation
This requires the attacker to control (or deploy) the `<ft-trait>` contract passed into `collateral-add`/`receive-tokens`, and to reenter `collateral-add` (via `market.clar`) for a first-time (unresolved) account, within the same top-level transaction, before the first call's `insert` executes. This is fully achievable in a single transaction with a purpose-built malicious FT contract implementing `ft-trait`'s `transfer` function to callback, matching the reachable single-transaction reentrancy interleaving described in the reference report.

### Recommendation
Move `insert updated-entry` (and any other persistence of the newly created id) to occur immediately after `resolve-or-create`/`create`, before the external `receive-tokens` call, so that a reentrant call resolves to the *same* id instead of creating a new one. Alternatively, add a reentrancy guard on `market-vault`'s `collateral-add`/`collateral-remove` (and the corresponding entrypoints in `market.clar`) so that nested calls for the same operation revert, consistent with the Checks-Effects-Interactions pattern already partially applied elsewhere in the codebase (e.g., the `in-flashloan` guards seen in the vault contracts).

### Proof of Concept
1. Deploy a malicious contract `Evil` implementing `ft-trait`, whose `transfer` function, on first invocation, calls back into `market.clar`'s collateral-add entrypoint for the same `account` with a second (small) amount and the same `Evil` contract as the `ft` parameter, then returns `(ok true)`.
2. Attacker (new account, not yet in `reverse`) calls `collateral-add`(`account`=attacker, `amount`=`amount1`, `ft`=`Evil`, `asset-id`=X) through `market.clar`.
3. During evaluation, `resolve-or-create` creates id `N1`, `add-user-collateral` credits `collateral[{N1,X}] += amount1`.
4. `receive-tokens` calls `Evil.transfer`, which reenters `collateral-add`(`account`=attacker, `amount`=`amount2`, `ft`=`Evil`, `asset-id`=X).
5. Inner call: `resolve-or-create` again sees no entry in `reverse` (outer `insert` not yet run), creates id `N2`, credits `collateral[{N2,X}] += amount2`, calls `receive-tokens`→`Evil.transfer` (returns immediately this time to avoid infinite recursion), then `insert` writes `reverse[attacker]=N2`.
6. Outer call resumes, finishes `receive-tokens`, then `insert` writes `reverse[attacker]=N1`, overwriting the inner mapping.
7. Query `get-position(attacker, ...)` — it resolves via `reverse[attacker]=N1`, showing only `amount1` collateral. `collateral[{N2,X}]` still holds `amount2` but is now permanently unreachable for `attacker`, demonstrating frozen funds.

### Citations

**File:** mainnet/contracts/market/v0-market-vault.clar (L137-157)
```text
(define-private (increment)
  (let ((curr (var-get nonce))
        (next (+ curr u1)))
    (var-set nonce next)
    curr))

(define-private (resolve-or-create (account principal))
  (let ((id? (map-get? reverse account)))
    (match id?
      id (lookup id)
         (create account)
    )))

(define-private (create (account principal))
  {
    id: (increment),
    account: account,
    mask: u0,
    last-update: stacks-block-time,
    last-borrow-block: u0
  })
```

**File:** mainnet/contracts/market/v0-market-vault.clar (L256-257)
```text
(define-private (receive-tokens (asset <ft-trait>) (amount uint) (account principal))
  (contract-call? asset transfer amount account current-contract none))
```

**File:** mainnet/contracts/market/v0-market-vault.clar (L374-404)
```text
(define-public (collateral-add (account principal) (amount uint) (ft <ft-trait>) (asset-id uint))
  (let ((states (var-get pause-states))
        (entry (resolve-or-create account))
        (user-id (get id entry))
        (mask (get mask entry))
        (updated-mask (mask-update mask asset-id true true)) ;; collateral, insert
        (updated-entry (merge entry (refresh updated-mask)))
        (result (add-user-collateral user-id asset-id amount)))

    (try! (check-impl-auth))
    (asserts! (not (get collateral-add states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)

    (try! (receive-tokens ft amount account))
    
    (insert updated-entry)

    (print {
      action: "collateral-add",
      caller: contract-caller,
      data: {
        account: account,
        asset-id: asset-id,
        amount: amount,
        updated-collateral-amount: result,
        mask-before: mask,
        mask-after: updated-mask
      }
    })
      
    (ok result)))
```
