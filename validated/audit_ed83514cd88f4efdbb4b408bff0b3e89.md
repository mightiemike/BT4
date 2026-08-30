### Title
Accrual pause pass-through freezes `index`/`lindex` but not `last-update`, causing an interest-rate time-jump on unpause - (`local-testing/contracts/vault/vault-stx.clar` / `mainnet/contracts/vault/v0-vault-stx.clar`)

### Summary
`accrue` in every vault (`vault-stx.clar`, `vault-sbtc.clar`, `vault-ststx.clar`, `vault-usdc.clar`, `vault-usdh.clar`, `vault-ststxbtc.clar`, and their mainnet `v0-*` counterparts) has a "pause" branch that returns the current cached `index`/`lindex` without reverting, mirroring the report's "cached value used instead of the true, freshly-derived state" bug class. [1](#0-0)  Because `last-update` is only advanced when `index` or `lindex` actually changes, the pause branch leaves `last-update` stale for the entire duration of the pause.

### Finding Description
`accrue` reads `pause-states` and, if `accrue` is paused, short-circuits with the currently stored `index`/`lindex` — a stale, cached snapshot of the interest state — without updating `last-update`: [2](#0-1) 

When accrual is later unpaused, `next-index` computes `time-delta = stacks-block-time - last-update`, which now spans the entire pause window plus any pre-pause elapsed time, and applies the full accrued interest rate over that entire span in a single accrual call: [3](#0-2) 

This is the same shape as the Yieldoor bug: a value (`index`/`lindex`, analogous to the Uniswap tick) is read from a cache/snapshot instead of being derived from the actual up-to-date state, and the code path that reads it does not invalidate/re-derive the timestamp that the next computation depends on. Here, `last-update` is the "clock" that is "advanced only on change" rather than every time `accrue` executes — precisely one of the listed valid analog patterns (a clock advanced only on change). The very first non-paused `accrue()` call after unpausing will use `time-delta` covering the whole pause duration, applying interest compounding as if the vault had continuously accrued during the pause, even though supply/borrow of underlying assets, and USD-notional health checks for borrowers, were frozen (paused) meanwhile with no corresponding rate accrual reflected in dependent contracts (e.g., zToken price caches used by `market.clar`'s `resolve-ztoken`, which reads `lindex` for pricing collateral). [4](#0-3) 

### Impact Explanation
Because the interest index (`index`) directly scales debt owed by borrowers (`total-debt`, `convert-to-scaled-debt`) and the liquidity index (`lindex`) directly scales the price of zTokens used as collateral in the market (`resolve-ztoken`), an unpause event produces a discontinuous, single-transaction jump in both debt-owed and collateral-value calculations. Any user (or a bot/liquidator) who can trigger the first post-unpause `accrue()`-touching call (deposit, borrow, repay, redeem, liquidation, or simply calling any of these functions) in the same block right after unpause can be first to interact under the old vs. new index, allowing them to borrow/withdraw or force liquidations based on a value that jumps discontinuously rather than accruing smoothly — this can freeze or misallocate borrower/lender yield (temporary freezing/misallocation of unclaimed yield), matching the in-scope "temporary freezing of funds"/"theft of unclaimed yield" impact class.

### Likelihood Explanation
This requires the DAO/operator to pause and then unpause the `accrue` flag on a vault — an intended, single administrative action, not a DAO compromise — after which the described jump occurs deterministically on the very first subsequent `accrue()` invocation in a single transaction. No attacker collusion or cross-user interference is needed; it's purely a consequence of how the pause interacts with the index/timestamp bookkeeping within one transaction. Likelihood is limited by the operational need to pause/unpause accrual, but the mechanism itself is fully in-scope, single-transaction, and reachable through normal user-facing vault entry points once unpaused.

### Recommendation
When accrual is paused, also update `last-update` to `stacks-block-time` (or freeze the elapsed-time accounting), so that when accrual resumes, `next-index`/`next-liquidity-index` compute interest strictly on the actual accruing window rather than including the paused duration. Alternatively, snapshot and persist an explicit "paused-at" timestamp and exclude the paused interval from `time-delta` calculations.

### Proof of Concept
1. Vault `vault-stx` accrues normally; `last-update = T0`, `index = I0`.
2. DAO pauses accrual (`pause-states.accrue = true`) at time `T1`. Any `deposit`/`borrow`/`redeem` call still invokes `accrue()`, which now takes the pause branch and returns `{index: I0, lindex: L0}` without updating `last-update` (still `T0`). [2](#0-1) 
3. Time passes; at `T2` (long after unpausing at `T1'`), DAO unpauses accrual.
4. Any user calls `borrow`/`redeem`/`deposit`, invoking `accrue()`. Since `accrue` is no longer paused, `next-index` is computed as `interest-rate() * (T2 - T0)` — i.e., the entire span from before the pause even started, through the whole pause window, is compounded in one shot. [5](#0-4) 
5. `index` and `lindex` jump discontinuously within this single transaction, changing debt owed by all borrowers and the USD value of zToken collateral used by `market.clar`'s health checks in the same block, before any user can react — enabling the caller of that transaction to be advantaged (e.g., borrowing against stale collateral value, or repaying debt just before the jump).

**Uncertainty:** I could not fully verify from the indexed snippets whether any additional safeguard elsewhere (e.g., a separate `last-update` reset call bundled with the pause/unpause DAO proposal) mitigates this in practice, since DAO proposal scripts for pause/unpause were not in the retrieved context. A full audit of `dao-executor`/proposal scripts that toggle `pause-states` would be needed to confirm whether `last-update` is reset atomically with the toggle.

### Citations

**File:** local-testing/contracts/vault/vault-stx.clar (L379-390)
```text
(define-private (next-index)
  (let ((states (var-get pause-states))
        (idx (var-get index)))
    (if (get accrue states)
        idx
        (let (
            (rate (interest-rate))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta rate time-delta true))))
          (calc-index-next idx multiplier)))))
```

**File:** local-testing/contracts/vault/vault-stx.clar (L835-863)
```text
(define-public (accrue)
  (let ((states (var-get pause-states))
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
                (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
                (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
                (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
                (treasury-lp (if (> reserve-inc u0) (mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc)) u0)))
            (if (not (is-eq idx next))
                (var-set index next)
                false)
            (if (not (is-eq lidx nliq))
                (var-set lindex nliq)
                false)
            (if (> treasury-lp u0)
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))
```

**File:** local-testing/contracts/market/market.clar (L365-369)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```
