### Title
Interest index `last-update` clock is only advanced when the computed index changes, letting a stale clock be reused with a new rate after a parameter change - (`mainnet/contracts/vault/v0-vault-stx.clar`)

### Summary
Every vault contract (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) tracks interest accrual with a data variable `last-update` that is supposed to represent "the last time the index was brought current." `accrue()` only advances `last-update` when the freshly computed index actually differs from the stored one: [1](#0-0) 

Rate-changing setters (`set-points-util`, `set-points-rate`, `set-fee-reserve`) call `(try! (accrue))` before mutating the rate curve/fee, intending to "checkpoint" all interest under the old parameters first, exactly like the recommended Angle mitigation: [2](#0-1) 

However, because `last-update` is only rewritten when the index actually moves, any period during which the computed multiplier equals `INDEX-PRECISION` (i.e., effective rate ≈ 0, which happens whenever the DAO configures a rate curve that is zero at the prevailing utilization, e.g. an emergency "pause interest" curve) leaves `last-update` frozen at its old timestamp while real time keeps passing. When rates are later restored to non-zero values, the very next `accrue()` computes `time-delta = stacks-block-time - last-update` using the *stale* `last-update`, but multiplies it by the *new* rate: [3](#0-2) 

This retroactively applies the new interest rate to a time window that occurred entirely under the old (zero) rate regime — the same root cause as the Angle `SavingsVest.accrue()` finding, where `vestingProfit`/`lastUpdate` were only refreshed when the collateral ratio moved more than a threshold, corrupting later vesting math after `vestingPeriod` changed.

### Finding Description
1. At time `T0`, `last-update = T0`, `index = I0`, curve has non-zero rates.
2. DAO calls `set-points-rate`/`set-points-util` with an all-zero (or effectively zero-at-current-utilization) rate curve to intentionally halt interest (e.g., emergency measure). This call's leading `(try! (accrue))` correctly checkpoints interest earned up to now: `next-index()` uses the OLD non-zero rate, index moves, `last-update` is set to `T1` (current block time).
3. From `T1` onward, `interest-rate()` returns 0 (or a value that, combined with `mul-div-up`/`mul-div-down` truncation, yields `multiplier == INDEX-PRECISION`) for every subsequent block. Consequently `next` == `idx` and `nliq` == `lidx` in `accrue()`, so the guard `(if (or (not (is-eq idx next)) (not (is-eq lidx nliq))) (var-set last-update stacks-block-time) false)` never fires — `last-update` stays pinned at `T1` no matter how many blocks/transactions elapse.
4. At time `T3` the DAO calls `set-points-rate` again to restore non-zero rates. The leading `(try! (accrue))` inside that call still sees the *old* (zero) rate (since `points-ir` hasn't been updated yet in that same call), so it computes `time-delta = T3 - T1` against a zero rate — no change, `last-update` is still not advanced, remaining at `T1`. Then `points-ir` is updated to the new non-zero curve.
5. At `T4`, any subsequent action (`system-borrow`, `system-repay`, `deposit`, `redeem`, `accrue`) triggers `next-index()`/`next-liquidity-index()`, which computes `time-delta = T4 - last-update = T4 - T1` (spanning the entire zero-rate window `T1→T3` plus the legitimate window `T3→T4`) and multiplies that whole span by the *new* rate. The zero-rate period is thus retroactively charged/credited interest at the new rate instead of 0, corrupting `total-debt`, `total-assets`, and the treasury `reserve-inc`/`treasury-lp` mint computed in `accrue()`.

### Impact Explanation
The mis-accrued interest changes `total-debt` (borrower obligations) and the liquidity index (depositor share value) and the treasury fee mint, all derived from the same `index`/`lindex`/`last-update` triple in every vault (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`). Depending on direction, depositors/lenders can be shorted unclaimed yield (if the "restored" rate is lower than what should have partially accrued) or borrowers can be overcharged retroactive interest for a period rates were meant to be zero — this falls in the "theft of unclaimed yield ... or temporary freezing of funds" impact band, since the affected value is interest yet to be claimed/settled, not principal at rest.

### Likelihood Explanation
Requires a DAO-governed rate-curve change to a curve that is (or trends toward) zero at prevailing utilization, followed later by restoration to a non-zero curve — a plausible operational sequence for a temporary "pause interest accrual" action, not a contrived edge case. It relies only on privileged/DAO parameter changes (in-scope, not a DAO-compromise scenario) and standard user activity between those changes; no collusion or two-user interference is needed.

### Recommendation
In `accrue()`, unconditionally set `last-update` to `stacks-block-time` whenever accrual logic runs (i.e., whenever `time-delta > 0`), regardless of whether `index`/`lindex` numerically changed, mirroring the Angle recommendation to always refresh the checkpoint on any parameter-affecting call rather than gating it on an observed-value-changed condition.

### Proof of Concept
Not independently executed against a live/test environment; the sequence above is derived directly from reading `accrue()`, `next-index()`, `next-liquidity-index()`, and the setter functions in `mainnet/contracts/vault/v0-vault-stx.clar` (identical logic is duplicated in the other vault contracts). Exploitability at scale (magnitude of the retroactive misapplication) depends on how far apart `T1` and `T3` can be in practice, which requires runtime/governance-cadence data not available from static code review alone.

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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L858-863)
```text
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))
```

**File:** mainnet/contracts/vault/v0-vault-usdh.clar (L664-690)
```text
(define-public (set-points-util (points (list 8 uint)))
    (let (
          (packed (unwrap-panic (pack-u16 points (some BPS))))
          (pir (var-get points-ir)))
      (try! (check-dao-auth))
      (try! (accrue))
      (var-set points-ir { util: packed, rate: (get rate pir) })
      
      (print {
        action: "vault-set-points-util",
        caller: tx-sender,
        data: {
          vault: UNDERLYING,
          points: points
        }
      })
      
      (ok true)))

(define-public (set-points-rate (points (list 8 uint)))
    (let (
          (packed (unwrap-panic (pack-u16 points none)))
          (pir (var-get points-ir)))
      (try! (check-dao-auth))
      (try! (accrue))
      (var-set points-ir { util: (get util pir), rate: packed })
      
```
