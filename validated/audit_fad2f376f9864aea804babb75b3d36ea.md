Note: index size limits mean I could not fully retrieve the body of `receive-tokens`/`send-tokens`, `add-user-collateral`, and `get-position`/`get-assets` in `market-vault.clar`; the analysis below is based on the ordering that is visible in the retrieved snippets. A full Devin session would be needed to confirm the exact bodies of these helper functions.

### Title
Collateral accounting mutated before the external token transfer completes, enabling reentrant over-borrowing during `collateral-add` - (File: `local-testing/contracts/market/market-vault.clar` / `mainnet/contracts/market/v0-market-vault.clar`)

### Summary
`collateral-add` in `market-vault.clar` writes the user's per-asset collateral balance to storage (via `add-user-collateral`, evaluated inside the function's `let` bindings) *before* it performs the external `contract-call?` that actually pulls the tokens in (`receive-tokens`), and only commits the position's bitmask (`insert updated-entry`) *after* that external call returns.

### Finding Description
```clarity
(define-public (collateral-add (account principal) (amount uint) (ft <ft-trait>) (asset-id uint))
  (let ((states (var-get pause-states))
        (entry (resolve-or-create account))
        ...
        (updated-entry (merge entry (refresh updated-mask)))
        (result (add-user-collateral user-id asset-id amount)))   ;; <-- STATE WRITE #1, evaluated here

    (try! (check-impl-auth))
    (asserts! (not (get collateral-add states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)

    (try! (receive-tokens ft amount account))                     ;; <-- EXTERNAL CALL (pulls tokens)

    (insert updated-entry)                                         ;; <-- STATE WRITE #2 (mask), only now
    ...
``` [1](#0-0) 

`add-user-collateral` (the accounting increment for the specific `asset-id`) is executed as part of the `let` binding, i.e. before `receive-tokens` — the `contract-call?` that actually transfers the underlying/zToken `ft` into the vault — is invoked. This is a checks/effects/interactions-order violation: the internal ledger for that asset is incremented before the value backing it is actually received, and the position bitmask is only committed after the external call returns.

If the asset-id being topped up is one the account already holds (mask bit already set from a prior deposit), then any code path that reads the account's position for that asset (e.g. `get-position`, `resolve-safe`, or the health-check flow used by `borrow`/`collateral-remove` in `market.clar`) would, during execution of `receive-tokens`, observe the already-incremented collateral balance even though the tokens have not yet left the caller's control. `receive-tokens` performs a `contract-call?` to an externally-specified `<ft-trait>` implementation; Clarity's contract-call graph does not enforce non-reentrancy, so if that trait implementation contains any callback logic that calls back into `market.clar`/`market-vault.clar` (e.g. `borrow`), the reentrant call would see the inflated collateral figure while the actual tokens are still mid-transfer.

The same before/after ordering issue can be seen in `collateral-remove`, but there it is the safer order (`insert` happens before `send-tokens`), which is the correct effects-then-interactions pattern — highlighting that `collateral-add`'s ordering is the inconsistent, riskier one. [2](#0-1) 

### Impact Explanation
If exploited via a reentrant `borrow` call during the token pull, an attacker could borrow against collateral that has not actually settled into the vault yet. Because the outer `collateral-add` transaction only becomes final if every step (including the external transfer) succeeds, the attacker needs the outer transfer to also complete successfully — meaning the practical value stolen is bounded by the temporary "double-counted" window, but it still represents direct extraction of protocol funds (over-borrowing against not-yet-received collateral), which falls under "theft of user funds at rest or in motion."

### Likelihood Explanation
Exploitability depends entirely on whether any DAO-registered collateral asset's `<ft-trait>` implementation contains re-entrant callback logic in its transfer path. Standard SIP-010 fungible tokens on Stacks have no ERC-777-style transfer hooks, so for the currently known assets (STX, sBTC, stSTX, USDC, USDH and Zest's own zTokens) there is no known vector to trigger the reentrant call. This mirrors the original Angle finding, which the Angle team itself called "really unlikely" since no credible ERC-777 collateral candidate existed, yet the finding was still confirmed valid because the ordering flaw is real and would be exploitable the moment such an asset is (or can be) accepted. The root-cause ordering bug here is real and independent of whether a hookable token exists today.

### Recommendation
Reorder `collateral-add` so that all storage effects (`add-user-collateral` and `insert updated-entry`, i.e. both the per-asset balance and the mask) are committed only after `receive-tokens` has successfully completed, matching the safer pattern already used in `collateral-remove` (state finalized before the external call). Alternatively, add an explicit reentrancy guard around `market-vault`'s state-mutating entry points reachable from a token transfer.

### Proof of Concept
1. DAO registers (or an existing custom/managed asset later gains) a `<ft-trait>` implementation whose `transfer` function contains logic that calls back into `market.clar`.
2. Attacker already holds asset-id `A` as collateral (mask bit set) with balance `X`.
3. Attacker calls `market.clar` `collateral-add` with `ft = A`, `amount = Y`, topping up existing collateral.
4. Inside `market-vault.clar` `collateral-add`, `add-user-collateral` immediately increments the stored balance for asset `A` to `X + Y` (evaluated in the `let` binding) before any external call occurs. [3](#0-2) 
5. `receive-tokens` is then invoked to actually pull `Y` tokens from the attacker; because asset `A`'s callback fires mid-transfer, the attacker re-enters `market.clar` `borrow`, whose health check reads the position and sees the already-updated balance `X + Y`, allowing the attacker to borrow against collateral not yet delivered.
6. `receive-tokens` completes as expected (attacker does have the funds to eventually transfer), the outer `insert updated-entry` commits the mask, and the whole transaction succeeds — leaving the attacker with both the extra borrowed funds and their original collateral tokens still in hand at the moment of the reentrant borrow.

### Citations

**File:** local-testing/contracts/market/market-vault.clar (L374-404)
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

**File:** local-testing/contracts/market/market-vault.clar (L406-438)
```text
(define-public (collateral-remove (account principal) (amount uint) (ft <ft-trait>) (asset-id uint) (recipient principal))
  (let ((states (var-get pause-states))
        (entry (resolve account))
        (user-id (get id entry))
        (mask (get mask entry))
        (remaining (try! (remove-user-collateral user-id asset-id amount)))
        (updated-mask (if (is-eq remaining u0)
                        (mask-update mask asset-id true false) ;; collateral, remove
                        mask))
        (updated-entry (merge entry (refresh updated-mask))))

    (try! (check-impl-auth))
    (asserts! (not (get collateral-remove states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)

    (insert updated-entry)
    (try! (send-tokens ft amount recipient))
    
    (print {
      action: "collateral-remove",
      caller: contract-caller,
      data: {
        account: account,
        recipient: recipient,
        asset-id: asset-id,
        amount: amount,
        updated-collateral-amount: remaining,
        mask-before: mask,
        mask-after: updated-mask
      }
    })
    
    (ok remaining)))
```
