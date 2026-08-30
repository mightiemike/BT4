### Title
`supply-collateral-add` grants the wrong asset-outflow allowance for the wSTX path, breaking the atomic supply-and-collateralize flow - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`supply-collateral-add` in `v0-4-market.clar` transfers an underlying token from the user with a generic `<ft-trait>` call, then, based solely on whether `ft-address` equals `ZEST-STX-WRAPPER-CONTRACT` (i.e. `.wstx`), it wraps the subsequent `vault-deposit` call in `(as-contract? ((with-stx amount)) ...)` instead of `(as-contract? ((with-ft ft-address "*" amount)) ...)`. [1](#0-0)  This assumes the STX vault consumes native STX, but `v0-vault-stx`'s `receive-underlying` performs a SIP-010 `transfer` on `.wstx` — a fungible-token outflow, not a native-STX outflow. [2](#0-1)  The allowance type declared (`with-stx`) does not match the asset actually moved out of the contract (an FT transfer of `.wstx`), which is the same root-cause class as the referenced BakerFi finding: the token approved/allowed for an operation is not the token actually consumed by that operation.

### Finding Description
1. A caller invokes `supply-collateral-add` with `ft` bound to the `.wstx` contract (the SIP-010 wrapped-STX token) and some `amount`.
2. The function resolves `ft-address = (contract-of ft)` and looks up the corresponding `asset-id` via `get-asset`, then pulls `amount` wSTX FT tokens from the user into the market contract via `(contract-call? ft transfer amount account current-contract none)`. [3](#0-2) 
3. Because `ft-address` equals `ZEST-STX-WRAPPER-CONTRACT`, the code branches into `(as-contract? ((with-stx amount)) (try! (vault-deposit asset-id amount min-shares account)))` — granting an outflow allowance for native µSTX. [4](#0-3) 
4. `vault-deposit` routes to `.v0-vault-stx deposit`, which calls `receive-underlying`, which performs `(contract-call? .wstx transfer amount account current-contract none)` — an FT transfer of the SIP-010 `.wstx` token out of the market contract, not a native STX transfer. [2](#0-1) 
5. The asset actually moved (`.wstx` FT) does not match the allowance type declared in the enclosing `as-contract?`/`with-stx` restriction (native STX). Per the Clarity 4 asset-restriction semantics documented in this repo's own reference material, any outflow not covered by a granted allowance causes the restricted block to revert. [5](#0-4) 

This mirrors the external report's root cause exactly: a token bound/approved at one point (`with-stx` allowance, analogous to `_asset`) diverges from the token actually consumed deeper in the call chain (the FT `.wstx` transfer inside `receive-underlying`, analogous to `loanToken`).

### Impact Explanation
Because Clarity public-function calls are atomic, the mismatched allowance causes the entire `supply-collateral-add` transaction to revert — it does not leak funds or strand value mid-flight; the user's initial `.wstx` transfer-in (step 2) is rolled back along with everything else. This means the impact is limited to the wSTX/STX code path of `supply-collateral-add` being permanently non-functional (a denial-of-service on this specific convenience entry point), not theft, insolvency, or fund freezing, since no state is retained on failure and users can still use the underlying `deposit` + `collateral-add` two-step flow to achieve the same outcome. This does not clear the bar for "Critical" or "High" impact as scoped by the rules (theft, insolvency, or freezing of funds/yield), since the failure mode is a full atomic rollback rather than a stranding of value.

### Likelihood Explanation
High likelihood of triggering: any user calling `supply-collateral-add(.wstx, amount, ...)` will hit this code path deterministically, with no special preconditions, attacker capital, or timing needed.

### Recommendation
In `supply-collateral-add`, use `(with-ft .wstx "*" amount)` (matching what `v0-vault-stx`'s `receive-underlying` actually consumes) instead of `(with-stx amount)` for the wSTX branch, or align the vault's `receive-underlying`/deposit flow with the allowance type declared by the caller. More generally, verify that every `as-contract?`/`with-*` allowance in the market contract matches the actual asset-transfer primitive used by the routed vault call, rather than branching on the source token's identity alone.

### Proof of Concept
Given the atomic-revert nature of the bug, no funds-at-risk PoC is possible; the observable behavior is a deterministic transaction failure:
1. Register `.wstx` as asset id `STX` in the asset registry (already the case per `v0-init.clar`).
2. User approves/holds `.wstx` SIP-010 balance and calls `(contract-call? .v0-4-market supply-collateral-add .wstx u1000000 u0 none)`.
3. Step 1 succeeds: `.wstx` is transferred from the user to the market contract.
4. Step 2 invokes `(as-contract? ((with-stx u1000000)) (vault-deposit STX u1000000 u0 account))`, which internally executes `.wstx transfer` (an FT outflow) inside a block that only grants a native-STX allowance.
5. The restricted block detects an unauthorized asset outflow (FT transfer not covered by the `with-stx` allowance) and reverts the entire `supply-collateral-add` call, including the earlier `.wstx` transfer-in from step 1.

Note: I was unable to fully verify the exact runtime revert code/error surfaced by `as-contract?`/`with-stx` violations from static reference material alone (the SIP-033 doc documents `restrict-assets?` explicitly but only briefly touches `as-contract?`'s identical restriction semantics); a Devin session with access to a Clarinet/Clarity simulator would be needed to execute this PoC and confirm the exact failure mode end-to-end.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L1176-1197)
```text
  (let ((ft-address (contract-of ft))
        (asset (try! (get-asset ft-address)))
        (asset-id (get id asset))
        (account contract-caller))
    
    ;; Preconditions
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (is-eq contract-caller tx-sender) ERR-AUTHORIZATION)
    
    ;; Step 1: Transfer underlying tokens from user to this contract (market)
    (try! (contract-call? ft transfer amount account current-contract none))
    
    ;; Step 2: Deposit to vault to get zTokens (minted to user)
    ;; Now the market has the underlying tokens and can call vault-deposit
    (let ((shares-minted 
            (try! (if (is-eq ft-address ZEST-STX-WRAPPER-CONTRACT)
              ;; For wSTX: use as-contract with-stx pattern
              (as-contract? ((with-stx amount))
                (try! (vault-deposit asset-id amount min-shares account)))
              ;; For other tokens: use as-contract with-ft pattern
              (as-contract? ((with-ft ft-address "*" amount))
                (try! (vault-deposit asset-id amount min-shares account)))))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L291-294)
```text
(define-private (receive-underlying (amount uint) (account principal))
  (begin
    (try! (contract-call? .wstx transfer amount account current-contract none))
    (ok true)))
```

**File:** local-testing/references/sip-033-clarity4.md (L174-187)
```markdown
  - **Description**: Executes the body expressions, then checks the asset
    outflows against the granted allowances, in declaration order. If any
    allowance is violated, the body expressions are reverted and an error is
    returned. Note that the `asset-owner` and allowance setup expressions are
    evaluated before executing the body expressions. The final body expression
    cannot return a `response` value in order to avoid returning a nested
    `response` value from `restrict-assets?` (nested responses are error-prone).
    Returns:

    - `(ok x)` if the outflows are within the allowances, where `x` is the
      result of the final body expression and has type `A`.
    - `(err index)` if an allowance was violated, where `index` is the 0-based
      index of the first violated allowance in the list of granted allowances,
      or `u128` if an asset with no allowance caused the violation.
```
