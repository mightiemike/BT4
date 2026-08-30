Found analog: the vault's `accrue` function updates `last-update` **only when the computed index actually changes**, per e.g. `mainnet/contracts/vault/v0-vault-usdh.clar` (`accrue`) and the shared `next-index` / `next-liquidity-index` helpers in `mainnet/contracts/vault/v0-vault-stx.clar:379-408` etc. This is directly analogous to the Magicsea bug class: a time-window guard (`time-delta = stacks-block-time - last-update`) is meant to invalidate a cached rate snapshot, but the invalidating clock (`last-update`) is advanced conditionally, not unconditionally, which is exactly the "clock advanced only on change" analog named in the rules.

### Title
Interest clock (`last-update`) only advances on index change, letting `time-delta` silently absorb multiple periods of zero-rate/zero-utilization time - (File: mainnet/contracts/vault/v0-vault-stx.clar)

### Summary
`next-index`/`next-liquidity-index` compute a `time-delta` against `(var-get last-update)` and derive a multiplier from it, then `accrue()` only calls `(var-set last-update stacks-block-time)` **if** the newly computed index differs from the stored one. When the interest rate is zero (idle utilization / rate curve producing 0 at that utilization point) the computed `next`/`nliq` equal the stored `idx`/`lidx`, so `last-update` is never advanced. This mirrors the report's core defect: a guard variable meant to be refreshed every time the underlying condition is evaluated is instead refreshed conditionally, letting a stale timestamp persist and be reused by a later evaluation under a different, exploitable condition.

### Finding Description
`accrue` in `mainnet/contracts/vault/v0-vault-usdh.clar:837-865` (and the sibling `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-ststxbtc.clar`, all sharing the same pattern) executes:
```
(if (not (is-eq idx next)) (var-set index next) false)
(if (not (is-eq lidx nliq)) (var-set lindex nliq) false)
...
(if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
    (var-set last-update stacks-block-time)
    false)
```
and `next-index` / `next-liquidity-index` (`mainnet/contracts/vault/v0-vault-stx.clar:379-408`) compute:
```
(time-delta (- stacks-block-time (var-get last-update)))
(multiplier (if (is-eq time-delta u0) INDEX-PRECISION (calc-multiplier-delta rate time-delta true)))
```
`time-delta` is the invalidating "clock" for the accrued-interest snapshot (`idx`/`lidx`). It is meant to represent "elapsed time since last accrual boundary" for every accrual call. But `last-update` is only bumped when the resulting index actually changes value. If the interest-rate curve yields `rate = 0` at the current utilization (e.g., zero debt / zero utilization segment of the points-rate curve, a normal operating state, not an admin action), `calc-multiplier-delta` returns a multiplier that leaves `next == idx` (and `nliq == lidx`), so the `if` bumps are both false and `last-update` is left unchanged.

Sequence:
1. Vault sits at 0% utilization; `accrue()` runs on every deposit/borrow etc., always computing `next-index`, but since rate is 0, `next == idx`; `last-update` stays at time T0.
2. Time passes (e.g., 30 days) with the vault still at 0% utilization (borrowers haven't drawn), so every `accrue()` call keeps recomputing `time-delta = now - T0` but never persists a new baseline, since the index doesn't move.
3. A borrower then borrows enough to push utilization to a non-zero rate in a single transaction. The very same `accrue()` call that processes this borrow now computes `time-delta` spanning the *entire* idle window since T0 (30 days) rather than the actual time since the previous evaluation, and applies the full-period compounding multiplier in one shot as if the non-zero rate had been active the whole time.
4. This is a single-transaction/single-block mutation-vs-guard ordering issue: the guard (`last-update`) that should bound how much interest can be posted per call is evaluated and updated based on a condition (`idx != next`) that is derived from the same multiplier it is meant to gate, so the accumulated "invisible" idle time is folded entirely into the transaction that first produces a non-zero rate.

This satisfies the "clock advanced only on change" bug-class analog: the clock (`last-update`) is the exact mechanism intended to invalidate/rebase the interest snapshot, but it is conditionally advanced rather than advanced on every evaluation, letting a large stale time window be absorbed into a single later accrual and applied at whatever rate is active at that moment (rather than the presumably-near-zero rate that was actually in effect during the idle window).

### Impact Explanation
Because `total-assets`/`total-debt`/liquidity-index compounding is what backs zToken redemption value (`vaults.md` Liquidity Index model) and debt owed (`vaults.md` Borrow Index model), a single accrual call that retroactively applies the current rate to a long stale window can mint disproportionate treasury shares (`reserve-inc`/`treasury-lp`) and inflate/deflate the index relative to what should have compounded incrementally. This can permanently misstate supplier/borrower balances - a form of temporary/permanent freezing or misallocation of unclaimed yield (interest that should have accrued gradually at the true historical rate is instead applied in a lump sum at a possibly much higher current rate), which falls under the in-scope "theft/freezing of unclaimed yield" impact class.

### Likelihood Explanation
Medium: the vault must sit at exactly zero computed rate/multiplier (idle utilization) for some duration and then transition to a non-zero rate in a subsequent transaction - a normal, not attacker-controlled, operating condition (a low-liquidity/low-utilization market is plausible, especially for newer or less popular assets: stSTXbtc, USDH). No admin misconfiguration or malicious actor is required; it can occur under ordinary idle-then-active usage patterns.

### Recommendation
Always update `last-update` to `stacks-block-time` on every `accrue()` call regardless of whether `idx`/`lidx` changed, so `time-delta` in the next call never spans a period during which accrual was already evaluated (even if it produced no numerical change).

### Proof of Concept
1. Deploy a vault with `points-rate` curve where the rate at utilization `u = 0` is `0`.
2. Call any operation that triggers `accrue()` (e.g., `deposit`) at `T0` with `debt = 0` → `next-index == idx`, `last-update` stays at its prior value `T_prev` (possibly far earlier).
3. Wait `N` days while utilization remains `0` (no borrow activity); repeatedly call `deposit`/`redeem` - each call computes `time-delta` growing but never persists a new `last-update` since index doesn't move.
4. At day `N`, a borrower calls `borrow()` large enough to make `utilization > 0`, causing `rate > 0`. The same `accrue()` call computes `time-delta = stacks-block-time - T_prev` (spanning the entire `N`-day idle period) and applies `calc-multiplier-delta(rate, time-delta)` as if the non-zero rate had applied for the whole `N` days, producing an oversized one-shot jump in `index`/`lindex` and `reserve-inc`/`treasury-lp` mint compared to incremental accrual. [1](#0-0) [2](#0-1)

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L379-404)
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

(define-private (next-liquidity-index)
  (let ((states (var-get pause-states))
        (lidx (var-get lindex)))
    (if (get accrue states)
        lidx
        (let (
            (rate (interest-rate))
            (liquidity-rate (calc-liquidity-rate rate (utilization) (var-get fee-reserve)))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta liquidity-rate time-delta false))))
          (calc-index-next lidx multiplier)))))
```

**File:** mainnet/contracts/vault/v0-vault-usdh.clar (L837-865)
```text
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

(define-public (system-borrow (amount uint) (receiver principal))
  (let (
      (states (var-get pause-states))
```
