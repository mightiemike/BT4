### Title
Collateral/debt state mutated in `let`-bindings before auth, pause, and amount guards execute in `collateral-add` / `debt-add-scaled` - (File: `mainnet/contracts/market/v0-market-vault.clar`)

### Summary
`collateral-add` and `debt-add-scaled` in `v0-market-vault.clar` compute their state mutation (`add-user-collateral` / `add-user-scaled-debt`) as a `let`-binding, which Clarity evaluates eagerly and in order *before* the function body's guard clauses (`check-impl-auth`, pause-state check, `amount > 0` check) run, and before the external, attacker-influenceable `receive-tokens` call to an arbitrary `<ft-trait>` contract executes.

### Finding Description
`collateral-add` binds `result` to `(add-user-collateral user-id asset-id amount)` inside its `let`, meaning the position's collateral map is updated as soon as this binding is evaluated: [1](#0-0) 

Only after this mutation has run does the function body execute the guards `(try! (check-impl-auth))`, `(asserts! (not (get collateral-add states)) ERR-PAUSED)`, `(asserts! (> amount u0) ERR-AMOUNT-ZERO)`, and then `(try! (receive-tokens ft amount account))` — the actual token pull from an arbitrary caller-supplied `<ft-trait>` contract. The bitmap/position record (`insert updated-entry`) is only written *after* the token transfer call returns.

`debt-add-scaled` follows the identical pattern: `add-user-scaled-debt` mutates the debt map inside the `let`, before `check-impl-auth`/pause/amount guards run in the body: [2](#0-1) 

This is the Clarity analog of "mutation evaluated before its guard": the accounting write is performed before the checks that are supposed to gate it, and before the external call to an untrusted token contract (`ft`) that can execute arbitrary logic during `transfer`. `receive-tokens`/`send-tokens` invoke `contract-call? ft transfer ...` on a caller-supplied principal that only needs to satisfy `<ft-trait>` — the market never restricts `ft` to a registered token address until `get-asset` is resolved one layer up in `market.clar`, and even there the resolved `asset-id` is trusted for the `market-vault` call without the vault itself re-deriving/re-checking it against `ft`.

Because the collateral map entry is already updated at the point `receive-tokens` runs, any reentrant call made from inside the malicious `ft` contract's `transfer` implementation (back into `market.clar`/`market-vault.clar` read paths, or into other public entry points) executes while the position already reflects credited collateral that has not yet been confirmed as actually transferred to the vault's balance, and before the pause/auth/zero-amount guards for *this* invocation have even had a chance to abort execution in cases where the whole call later fails partway (e.g., a subsequent `try!` in the same call unwraps an error unrelated to this early mutation, but a nested contract-call already used the intermediate state via a non-error-propagating `match`).

### Impact Explanation
If the intermediate, unguarded mutation is observed or acted upon by a reentrant call before the surrounding checks/rollback take effect, it can let a malicious token contract get collateral credited to a position without a fully validated deposit, or cause the debt/collateral ledger to diverge from actual token balances held in `market-vault`/vaults — a direct accounting-integrity break that maps to theft/insolvency risk (Critical) since a mismatched ledger can be leveraged to borrow against unbacked collateral.

### Likelihood Explanation
Likelihood depends on whether Clarity's transaction-level atomic rollback fully neutralizes the intermediate state exposure in all call paths (in most cases it does, since a later `asserts!`/`try!` failure unwinds the whole transaction). The residual risk is limited to any nested contract-call from within the malicious `ft` contract that is captured with `match`/`ok`-only handling elsewhere in the protocol rather than propagated with `try!`, letting a side effect from the reentrant call commit independent of this call's final outcome. This requires a specific interleaving that could not be fully confirmed by static inspection alone.

### Recommendation
Move all state mutations (`add-user-collateral`, `add-user-scaled-debt`, and their `collateral-remove`/`debt-remove-scaled` counterparts) out of `let`-bindings and into the function body **after** all guard clauses (`check-impl-auth`, pause-state, amount checks) and after any external `contract-call?` to a caller-supplied trait, so that Clarity's evaluation order matches the intended check-then-effect sequence, and add an explicit assertion in `market-vault` that the `asset-id` argument matches `(get-asset (contract-of ft))` rather than trusting the value forwarded by `market.clar`.

### Proof of Concept
1. Attacker deploys a contract `Evil` implementing `<ft-trait>` whose `transfer` function, when called, issues a nested `contract-call?` back into `market.clar`/`market-vault.clar` (e.g., a read-only position query or another public entry point) before returning.
2. Attacker calls `collateral-add` (via `market.clar`) with `ft = Evil` and a chosen `asset-id`.
3. In `v0-market-vault.clar`'s `collateral-add`, the `let` binding `result = (add-user-collateral user-id asset-id amount)` executes and mutates the `collateral` map immediately.
4. Guards (`check-impl-auth`, pause, `amount > 0`) then run; assuming they pass, `(try! (receive-tokens ft amount account))` calls `Evil.transfer`.
5. `Evil.transfer` triggers its nested call, observing/using the already-mutated collateral state for `account` before the underlying tokens have verifiably moved into the vault and before `(insert updated-entry)` persists the final mask, exploiting the gap between mutation and guard/finalization.

### Citations

**File:** mainnet/contracts/market/v0-market-vault.clar (L374-389)
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
```

**File:** mainnet/contracts/market/v0-market-vault.clar (L442-456)
```text
(define-public (debt-add-scaled (account principal) (scaled-amount uint) (asset-id uint))
  (let ((states (var-get pause-states))
        (entry (resolve-or-create account))
        (user-id (get id entry))
        (mask (get mask entry))
        (update-mask (mask-update mask asset-id false true)) ;; debt, insert
        ;; Oracle frontrunning protection: record current block when borrowing
        (updated-entry (merge entry { mask: update-mask, last-update: stacks-block-time, last-borrow-block: stacks-block-height }))
        (result (add-user-scaled-debt user-id asset-id scaled-amount)))

    (try! (check-impl-auth))
    (asserts! (not (get debt-add states)) ERR-PAUSED)
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

    (insert updated-entry)
```
