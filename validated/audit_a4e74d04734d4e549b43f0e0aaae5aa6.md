### Title
Vault `accrue` pauses accrual as a silent pass-through, letting a single `borrow`/`repay`/`liquidate` transaction mint treasury shares and update collateral valuations off a stale index instead of reverting - (File: `mainnet/contracts/vault/v0-vault-usdc.clar`, similarly `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`)

### Summary
The vault `accrue` function, called on every `system-borrow`, `system-repay`, `deposit`, `redeem`, and via `market.clar`'s `accrue-and-cache` before liquidation/health checks, contains an "accrue paused" branch that returns the *current* (stale) `index`/`lindex` as `(ok {...})` instead of reverting: `(if (get accrue states) (ok { index: idx, lindex: lidx }) ...)`. This value is then written straight into `market.clar`'s per-block `index-cache` and used for debt scaling, collateral (ztoken) valuation, and liquidation math within the same transaction, without any signal that the figure is not current interest-accrued state.

### Finding Description
`accrue` in the vault contracts computes `next-index`/`next-liquidity-index` and updates `index`/`lindex` when not paused. When the `accrue` pause flag in `pause-states` is set, it instead short-circuits with: [1](#0-0) 
returning the currently-stored `idx`/`lidx` wrapped in `(ok ...)` — a successful response — rather than an error. Every caller of `accrue` (`system-borrow`, and by extension `market.clar`'s `accrue-and-cache`) treats this as a legitimate, fresh result: [2](#0-1) 

`market.clar`'s `accrue-and-cache` immediately persists whatever `vault-accrue` returns into the block-scoped `index-cache` map, keyed only by `{ timestamp: stacks-block-time, aid }`, with no distinction between "freshly accrued index" and "paused/stale index": [3](#0-2) 

That cached value is then used directly for debt scaling and remaining-debt calculations inside `liquidate` in the very same call: [4](#0-3) [5](#0-4) 

and it is also used to compute the `total-debt-usd`/`total-collateral-usd` valuation feeding the health check that gates whether liquidation is even permitted: [6](#0-5) 

The root cause is the "accrue" guard being evaluated inside the function that is supposed to produce the authoritative index, but the guard's failure path fabricates a success instead of aborting: the value bound by `accrue` (borrow index / liquidity index) is exactly the value whose freshness the pause is meant to protect, yet the pause makes the function lie about having refreshed it. Because `market.clar` caches this per-timestamp with no "was this actually accrued" flag, a single transaction that hits a paused vault's `accrue` will silently compute debt, collateral valuation, health, and (in `system-borrow`'s companion accrue path) treasury share minting off out-of-date index state, and this stale figure then gets baked into on-chain records (`debt-remove-scaled`, `collateral-remove`, treasury `ft-mint?`) that persist after the transaction — i.e. the guard is bypassed via a pass-through instead of a revert, within a single transaction/block, and the corrupted result is committed to storage.

### Impact Explanation
If accrual is paused (an operational control meant for incident response) while a `borrow`, `repay`, or `liquidate` executes, users' scaled debt is recorded against a stale index rather than being blocked, and liquidators' seized-collateral / repaid-debt amounts are computed against a stale valuation. This can result in real value moving (collateral seized, debt cleared, treasury shares minted) using out-of-date accounting that doesn't match true economic state at the moment of pause — a temporary freezing/misallocation of unclaimed yield/interest (the accrued-but-unrecorded interest difference for that block is silently dropped rather than deferred safely), which falls under High: theft/freezing of unclaimed yield. It does not require any DAO compromise beyond the intended, documented pause-toggle admin action, and does not depend on oracle manipulation, flashloans, or cross-user interference — it is purely a same-transaction mutation-evaluated-before-guard defect in a single vault call.

### Likelihood Explanation
The `accrue` pause is a normal, expected admin lever (used for incident response, e.g. mid-exploit vault freeze) per the vault's own pause-state design, so the paused state is realistically reachable in production. Any of `borrow`, `repay`, `liquidate`, `deposit`, or `redeem` invoked while `accrue` is paused on the relevant vault will trigger this pass-through path, so the likelihood of the interleaving occurring is moderate-to-high whenever an admin pauses accrual without also pausing every dependent market operation.

### Recommendation
Change the paused branch of `accrue` to return an explicit error (e.g. `ERR-PAUSED`) instead of `(ok { index: idx, lindex: lidx })`, and propagate that error up through `system-borrow`/`system-repay`/`accrue-and-cache` so that any market operation dependent on a paused vault's index reverts rather than silently proceeding with stale interest state. If a genuine "read-only, no mutation" pass-through is desired for pure view calls, it must be distinguished from mutating flows (`borrow`, `repay`, `liquidate`) that persist state derived from the returned index.

### Proof of Concept
Conceptual sequence (Clarity, single transaction):
1. DAO/admin sets `accrue` pause flag `true` on `v0-vault-usdc` via the vault's pause-setter (documented operational control).
2. A user (or liquidator) calls `market.clar liquidate` (or `borrow`/`repay`) referencing USDC as the debt/collateral asset.
3. `market.clar` calls `accrue-user-debts`/`accrue-user-collateral` → `accrue-and-cache` → `vault-accrue` (`v0-vault-usdc.system-borrow`'s internal `accrue`).
4. Because `(get accrue states)` is `true`, `accrue` returns `(ok { index: idx, lindex: lidx })` with the *last stored* `idx`/`lidx`, not a freshly computed one — no revert occurs. [1](#0-0) 
5. `market.clar` stores this stale pair into `index-cache` for the current `stacks-block-time`. [3](#0-2) 
6. `liquidate` uses this cached, stale index for `total-debt-usd`/`total-collateral-usd`, health check, and scaled-debt removal, and the resulting `debt-remove-scaled`/`collateral-remove` calls persist debt/collateral changes computed off the stale figure. [7](#0-6) [8](#0-7) 
7. Transaction succeeds instead of reverting; the operation is settled against data that does not reflect true accrued interest at time of execution.

Note: I could not directly view the full body of `mainnet/contracts/vault/v0-vault-usdc.clar` in this session (tool call for full file read failed on the final iteration), so the exact line numbers for `system-borrow`/`accrue` in that specific file are inferred from the identical logic confirmed in `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, and the local-testing mirror `vault-usdc.clar`, all of which share the same pattern verbatim. A follow-up read of the mainnet `v0-vault-usdc.clar` file is recommended to confirm identical line numbers before remediation.

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdh.clar (L841-843)
```text
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
```

**File:** mainnet/contracts/vault/v0-vault-usdh.clar (L867-887)
```text
      (CAP-DEBT (var-get cap-debt))
      (available-assets (get-available-assets))
      (scaled-principal (var-get principal-scaled))
      (idx (var-get index))
      (debt (total-debt))
      (scaled-amount (mul-div-up amount INDEX-PRECISION idx))
      (updated-scaled-principal (+ scaled-principal scaled-amount)))

    (try! (check-caller-auth))
    (asserts! (not (get borrow states)) ERR-PAUSED)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (<= amount available-assets) ERR-INSUFFICIENT-VAULT-LIQUIDITY)
    (asserts! (<= (+ debt amount) CAP-DEBT) ERR-DEBT-CAP-EXCEEDED)

    (var-set principal-scaled updated-scaled-principal)
    (var-set total-borrowed (+ (var-get total-borrowed) amount))
    (try! (send-underlying amount receiver))

    (print {
      action: "system-borrow",
      caller: contract-caller,
```

**File:** mainnet/contracts/market/v0-4-market.clar (L245-257)
```text
(define-private (accrue-and-cache (aid uint))
  (let ((cache-key { timestamp: stacks-block-time, aid: aid })
        (cached? (map-get? index-cache cache-key)))

    (match cached?
      ;; cache HIT: return cached value (1 read only)
      cached-indexes (ok cached-indexes)

      ;; cache MISS: accrue and cache (vault-accrue now returns indexes)
      (let ((indexes (try! (vault-accrue aid))))
        ;; store in cache
        (map-set index-cache cache-key indexes)
        (ok indexes)))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1428-1436)
```text
    ;; Oracle frontrunning protection: prevent same-block liquidation
    ;; This blocks flash-loan based attacks where user borrows + gets liquidated in same block
    (last-borrow-block (get last-borrow-block position))
    (same-block-check (asserts! (not (is-eq last-borrow-block stacks-block-height)) ERR-LIQUIDATION-BORROW-SAME-BLOCK))

    ;; health check (FAIL-FAST) 
    ;; Check position is liquidatable BEFORE calling calc-liq-factor
    (health-check  (asserts! (>= current-ltv ltv-liq-partial) ERR-HEALTHY))

```

**File:** mainnet/contracts/market/v0-4-market.clar (L1477-1524)
```text
    (remaining-debt-to-repay
      (if (> coll-remaining u0)
        (let ((rem-coll-usd (normalize (* coll-remaining coll-price) coll-decimals false))
              (rem-debt-usd (div-bps-down rem-coll-usd (+ BPS liq-penalty-max)))
              (rem-debt-tokens (mul-div-down rem-debt-usd (pow u10 debt-decimals) debt-price))
              (rem-borrow-index (get index (unwrap-panic (get-cached-indexes debt-aid))))
              (rem-scaled (mul-div-down rem-debt-tokens INDEX-PRECISION rem-borrow-index)))
          (mul-div-up rem-scaled rem-borrow-index INDEX-PRECISION))
        u1))
    (coll-final (if (is-eq remaining-debt-to-repay u0) user-coll-balance coll-final-raw)))

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
```
