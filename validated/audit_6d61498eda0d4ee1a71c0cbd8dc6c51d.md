Found the relevant analog: in `system-repay`, the `debt` value (from `total-debt`) is used as an unguarded divisor twice — once inside `calc-principal-ratio-reduction` and once directly in `mul-div-down capped-amount total-borrowed-amount debt` — with no zero-check, unlike the sibling function `socialize-debt` in the same contract, which explicitly guards the analogous division with `(if (> scaled-principal u0) ... u0)`.

### Title
Division by zero in `system-repay` when `total-debt` is zero freezes repayments for vaults with residual scaled principal - (File: `mainnet/contracts/vault/v0-vault-sbtc.clar`, `v0-vault-stx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`)

### Summary
`system-repay` computes `debt` via `total-debt` and then divides by it twice without checking for zero, unlike the sibling `socialize-debt` function in the same file which explicitly guards an equivalent division.

### Finding Description
`total-debt` is defined as `(calc-cumulative-debt (var-get principal-scaled) (var-get index))`, i.e. `mul-div-up principal-scaled idx INDEX-PRECISION` [1](#0-0) . In `system-repay`, `debt` is bound from `total-debt` at the top of the `let`, and then used as the divisor in two places without any zero-guard: `calc-principal-ratio-reduction capped-amount scaled-principal debt` and `mul-div-down capped-amount total-borrowed-amount debt` [2](#0-1) . `calc-principal-ratio-reduction` itself is a bare `mul-div-down amount scaled-principal debt-amount` with no zero check [3](#0-2) , and Clarity's `/` operator reverts (runtime error) when the divisor is `u0`.

`principal-scaled` can be non-zero while `total-debt` (a `mul-div-up`) rounds/evaluates to `u0` only in degenerate edge conditions (e.g., extremely small `principal-scaled` combined with `index`), but more importantly, `debt` is computed fresh in the same transaction after `accrue` runs; if `principal-scaled` is `u0` (fully repaid/socialized vault) while `total-borrowed` is stale or a caller still calls `system-repay` with `amount > 0`, `total-debt` evaluates to `u0`, and the function reverts instead of handling the no-outstanding-debt case gracefully. This mirrors the reported bug class: a shared state value (`debt`/`total-debt`) is read once, used as a divisor in multiple places, and the code path that legitimately reaches "denominator is zero" (e.g., after `socialize-debt` has already zeroed `principal-scaled` for a vault in the same or a prior transaction) is not defended, in contrast to the explicit `(if (> scaled-principal u0) ... u0)` guard applied to the structurally identical division in `socialize-debt` just below it in the same file [4](#0-3) .

### Impact Explanation
If `total-debt` is zero at the moment `system-repay` executes (e.g., after the vault's debt was fully socialized/repaid but a repay call is still in flight, or `scaled-principal` rounds to leave `total-debt` at zero while `total-borrowed` is nonzero from a stale accounting path), every subsequent call to `system-repay` for that vault reverts. Since `system-repay` is the only path to reduce `principal-scaled`/`total-borrowed` and unstick residual bookkeeping, this results in a temporary freezing of funds/settlement for the affected vault until the underlying state changes another way — matching the in-scope "temporary freezing of funds" impact class.

### Likelihood Explanation
This requires reaching a state where `principal-scaled` is zero (or debt rounds to zero) while the market still routes a `system-repay` call with `amount > 0` to the vault — a state reachable through the existing `socialize-debt` function or extreme rounding at very low principal, both of which are normal (non-privileged) code paths already present in the contract, so no DAO compromise or malicious oracle input is required.

### Recommendation
Guard the divisions in `system-repay` the same way `socialize-debt` guards its analogous division: check `(> debt u0)` before calling `calc-principal-ratio-reduction` and before the `mul-div-down capped-amount total-borrowed-amount debt` computation, returning early with `(ok true)`/no-op or a capped-amount of `u0` when `debt` is zero.

### Proof of Concept
1. Vault accumulates debt; `principal-scaled` and `total-borrowed` are non-zero.
2. `socialize-debt` is called (e.g., due to a shortfall), setting `principal-scaled` to `u0` while `total-borrowed` is reduced but not necessarily to zero in the same call, per the branch `(var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))` [5](#0-4) .
3. A caller (e.g., the market contract on behalf of a user finishing a repay flow) then calls `system-repay(amount)` with `amount > 0` for that vault.
4. `total-debt` evaluates to `u0` since `principal-scaled` is `u0` [1](#0-0) .
5. `capped-amount` becomes `debt` = `u0` per `(if (> amount debt) debt amount)`, but `mul-div-down capped-amount total-borrowed-amount debt` still divides by `debt = u0`, causing a runtime division-by-zero revert [6](#0-5) , aborting the repay transaction and freezing that code path for the vault until the invariant is externally corrected.

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L192-193)
```text
  (mul-div-down amount scaled-principal debt-amount))

```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L326-331)
```text
(define-private (total-debt)
  (calc-cumulative-debt (var-get principal-scaled) (var-get index)))

(define-private (debt-preview)
  (calc-cumulative-debt (var-get principal-scaled) (next-index)))

```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L900-914)
```text
(define-public (system-repay (amount uint))
  (let (
        (states (var-get pause-states))
        (u (try! (accrue)))
        (scaled-principal (var-get principal-scaled))
        (idx (var-get index))
        (debt (total-debt))
        (total-borrowed-amount (var-get total-borrowed))
        (capped-amount (if (> amount debt) debt amount))
        (principal-reduction (calc-principal-ratio-reduction capped-amount scaled-principal debt))
        (capped-reduction (if (> principal-reduction scaled-principal) scaled-principal principal-reduction))
        (updated-scaled-principal (- scaled-principal capped-reduction))
        (principal-repaid (mul-div-down capped-amount total-borrowed-amount debt))
        (interest-paid (- capped-amount principal-repaid))
        (total-borrowed-new (if (> total-borrowed-amount principal-repaid) (- total-borrowed-amount principal-repaid) u0)))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L942-956)
```text
(define-public (socialize-debt (scaled-amount uint))
  (let ((scaled-principal (var-get principal-scaled))
        (borrowed (var-get total-borrowed))
        (idx (var-get index))
        (current-assets (var-get assets))
        (current-lindex (var-get lindex))
        (old-total-assets (total-assets))
        (debt-reduction (mul-div-down scaled-amount idx INDEX-PRECISION))
        (principal-reduction (if (> scaled-principal u0)
                                (mul-div-down scaled-amount borrowed scaled-principal)
                                u0))
        ;; Write down lindex proportionally to loss in total-assets
        (new-lindex (if (and (> old-total-assets u0) (> old-total-assets debt-reduction))
                       (mul-div-down current-lindex (- old-total-assets debt-reduction) old-total-assets)
                       u0)))
```

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L962-964)
```text
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))
```
