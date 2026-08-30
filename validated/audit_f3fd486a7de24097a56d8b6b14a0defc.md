## Title
Bad-debt socialization failure inside `liquidate-multi` silently absorbed, leaving seized collateral committed while debt write-down is skipped — (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`liquidate-multi` invokes `liquidate` through a **same-contract, non-`contract-call?` function call** (`call-liquidate` → `liquidate`), then collects every result with `map` and unconditionally returns `(ok ...)`. Because `liquidate` is not entered through a `contract-call?` boundary, any nested cross-contract mutations it performs (`vault-system-repay`, `market-vault debt-remove-scaled`, `market-vault collateral-remove`) commit individually and permanently the moment each one returns `ok`, regardless of what `liquidate` itself returns afterward. If the later bad-debt-socialization step — a `fold` over `socialize-debt-asset` — fails on any entry, `liquidate` returns an error, but because `liquidate-multi` never propagates it (it is wrapped inside the always-`ok` list), the whole transaction still commits: the liquidator has already been repaid, the borrower's collateral has already been seized, but the write-down of the borrower's remaining (now unbacked) debt across other assets is skipped for that borrower and all later entries in the fold.

### Finding Description
In `liquidate`: [1](#0-0) 
the debt is repaid (`vault-system-repay`), the borrower's debt is decremented (`debt-remove-scaled`), and collateral is transferred to the liquidator (`collateral-remove`) — all via individually-committing `contract-call?`s — *before* the fold that performs bad-debt socialization for any remaining, now-unbacked debt: [2](#0-1) 

The fold helper is: [3](#0-2) 
Each iteration calls the vault's `socialize-debt`, which itself hard-reverts on a zero scaled amount: [4](#0-3) 
If any entry in `fresh-debt-list` fails this check (e.g. a dust debt entry whose scaled amount rounds to `u0`, or any other transient failure of `vault-socialize-debt`/`vault-accrue`/`debt-remove-scaled` for that entry), `unwrap!` immediately short-circuits `socialize-debt-asset`, and every subsequent entry in the fold is skipped via the early-return guard `(if (not (get success acc)) acc ...)`. The outer function then asserts on this failure: [5](#0-4) 
causing `liquidate` to return `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)`.

Crucially, `liquidate-multi` never checks this error — it wraps every position's result unconditionally: [6](#0-5) 
The comment even documents the intended (but here abused) semantics: *"Failed liquidations return error codes but don't revert entire batch."* Because `liquidate` is reached via `call-liquidate` as an ordinary same-contract function call (no `contract-call?` wrapper), Clarity's automatic sub-transaction rollback — which only applies at `contract-call?` boundaries — does **not** apply to `liquidate`'s own body. Every nested `contract-call?` that already returned `ok` earlier in `liquidate`'s execution (debt repayment, debt removal, collateral removal) is permanently committed once the top-level `liquidate-multi` transaction itself returns `ok`. The write-down that should accompany the removed collateral (via `vault-socialize-debt`, which reduces `lindex` to reflect the loss to suppliers) never happens for the affected asset(s).

### Impact Explanation
This is a "fold that absorbs failure" scenario: collateral is transferred out and the primary debt leg is reduced, but the compensating write-down (`socialize-debt` reducing the vault's `lindex`) for the remaining, now-fully-unbacked debt on other assets is skipped. That debt stays on the borrower's books (uncollectible, since all collateral is gone) while the vault's accounting still treats it as good debt, meaning zToken (share) values for that vault remain inflated relative to real backing. This is a protocol-insolvency-class bug: the pool's liabilities silently exceed its assets, and suppliers of that vault absorb an undisclosed, un-socialized loss that is never marked down — eventually manifesting as inability to fully redeem shares (Critical: protocol insolvency / permanent freezing of funds).

### Likelihood Explanation
`liquidate-multi` is a normal, public, permissionless entry point intended to batch liquidations across positions/borrowers. A borrower (or an attacker orchestrating the sequence) only needs one entry in their debt list whose scaled amount evaluates to `u0` (achievable through ordinary rounding after partial repayments/accruals) at the moment a batched full liquidation drains their last collateral. No privileged access or DAO action is required; the failure path is reachable purely through the existing public liquidation flow's `map`-over-`liquidate` pattern.

### Recommendation
- In `liquidate-multi`, propagate/require success rather than absorbing errors when a position's socialization step fails after collateral has already been seized (e.g., require `liquidate` to fully revert its own nested effects on socialization failure, by moving socialization to run via a genuine `contract-call?` boundary, or by not seizing collateral/repaying debt until socialization is guaranteed to succeed).
- Ensure `socialize-debt-asset`/`fresh-debt-list` filters out zero-scaled entries before folding, so `ERR-AMOUNT-ZERO` in the vault's `socialize-debt` cannot be triggered by dust residue.
- More generally, any public function whose body performs multiple sequential `contract-call?`s that mutate external contract state should not be invoked through a same-contract direct call from a `map`/`fold`-based batch entry point that discards errors — batch orchestration should use `contract-call?` (self-call) so that failures automatically roll back the position's partial state changes.

### Proof of Concept
1. Borrower deposits collateral and opens two debt positions: a primary sizable debt (asset A) and a dust debt (asset B) whose scaled remainder is a value that, due to rounding in a prior partial repay/accrual, is stored/derived as `u0` scaled units (or otherwise causes a subsequent vault call inside `socialize-debt-asset` to fail).
2. Borrower's collateral value drops (or a large enough single-asset liquidation is requested) such that a liquidator calls `liquidate-multi` targeting this borrower with debt-amount sized to fully drain the collateral for asset A, making `no-collateral-left` true.
3. Inside `liquidate`, `vault-system-repay`, `market-vault debt-remove-scaled` (asset A), and `market-vault collateral-remove` all succeed and commit — the liquidator receives the seized collateral and asset A's debt-repaid amount is finalized.
4. `fresh-debt-list` (asset A remainder + asset B) is folded via `socialize-debt-asset`; the asset B entry with scaled `u0` triggers `ERR-AMOUNT-ZERO` inside the vault's `socialize-debt`, causing `unwrap!` to short-circuit with `failed-status`.
5. `(asserts! (get success socialization-result) ERR-BAD-DEBT-SOCIALIZATION-FAILED)` fires, and `liquidate` returns `(err ERR-BAD-DEBT-SOCIALIZATION-FAILED)`.
6. `call-liquidate` returns this err value unchanged; `liquidate-multi`'s `(ok (map call-liquidate positions))` still returns `ok` for the whole transaction — the transaction commits.
7. Result: liquidator keeps the seized collateral and the debt-repayment credit for asset A, but asset B's debt (now fully unbacked, since the borrower has zero collateral) remains on the books without any corresponding `lindex` write-down in vault B — an un-socialized bad debt permanently inflating vault B's supply-side accounting.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1495-1533)
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

```

**File:** mainnet/contracts/market/v0-4-market.clar (L1535-1560)
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
                        ;; emit bad-debt-socialized event
                        (print {
                          action: "bad-debt-socialized",
                          caller: contract-caller,
                          data: {
                            borrower: borrower,
                            debt-list: fresh-debt-list
                          }
                        })
                        true)
                      false))
                  false)))
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

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L958-965)
```text
    (try! (check-caller-auth))
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

    (var-set lindex new-lindex)
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))

```
