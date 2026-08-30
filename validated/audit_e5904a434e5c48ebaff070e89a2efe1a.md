### Title
Frequent low-latency `accrue()` calls let a shared `last-update` timestamp advance on borrow-index rounding while the liquidity-index rounds down to zero, permanently starving depositors of interest - ([File: mainnet/contracts/vault/v0-vault-usdc.clar])

### Summary
In `v0-vault-usdc.clar` (and the other vault contracts sharing this template, e.g. `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`), the `accrue` function computes two independent multiplicative indexes — the borrow `index` (rounded **up**) and the liquidity `lindex` (rounded **down**) — from the same elapsed time delta, but both are gated by a single shared `last-update` timestamp that is reset whenever **either** index changes. [1](#0-0) 

### Finding Description
`next-index` and `next-liquidity-index` both derive their multiplier from `calc-multiplier-delta`, using `time-delta = stacks-block-time - last-update`: [2](#0-1) 

The borrow-side multiplier rounds **up** (`round-up = true`), while the liquidity-side multiplier rounds **down** (`round-up = false`), and the liquidity rate itself is a fraction of the borrow rate (scaled by utilization and `1 - reserve-factor`): [3](#0-2) 

Because `calc-multiplier-delta` for the borrow index rounds up, even a 1-2 second time delta can nudge `index` by +1 wei of `INDEX-PRECISION` (1e12), causing the `(not (is-eq idx next))` branch to fire. Since `lindex`'s rate is always smaller (utilization% × (1-reserve-factor)% of the borrow rate) and rounds down, the same tiny time delta almost always yields `nliq == lidx` (no change). The `accrue` function only resets `last-update` when *either* index changed:

```
(if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
    (var-set last-update stacks-block-time)
    false)
``` [4](#0-3) 

So whenever the borrow-index rounding alone causes a change, `last-update` advances to `stacks-block-time` even though the liquidity index (which determines depositor/zUSDC-holder yield) did not increase for that window. The elapsed time credited toward the depositors' next interest computation is discarded — it is never "carried over," because `time-delta` for the *next* call is always computed from the new `last-update`, not from any accumulated remainder.

### Impact Explanation
An attacker (or, per the referenced report, sufficiently frequent organic activity) can call any function that triggers `accrue()` — e.g. `system-borrow`, `system-repay`, deposit/redeem paths — every block or every few blocks. Each call is likely to tick the borrow `index` by its rounded-up minimal increment while the liquidity `lindex` stays flat (rounded down to 0 change), yet the shared `last-update` clock is reset regardless. This permanently starves zUSDC/zUSDH/zstSTX depositors of the interest they are entitled to for as long as the attacker keeps forcing sub-threshold accrual calls, while borrowers' debt still creeps up from the borrow-side rounding. This is a High-severity issue: theft/permanent freezing of unclaimed yield owed to liquidity providers, directly analogous to the Kwenta USDC `rewardPerToken` precision bug where frequent updates round distributable rewards to zero.

### Likelihood Explanation
The attack requires only ordinary permissionless calls that trigger `accrue()` (borrow/repay/deposit/redeem are not gated to privileged callers) at a cadence of roughly 1 call per block/few blocks. No governance, oracle manipulation, or privileged access is needed — only enough capital or authorization to make cheap borrow/repay round-trips (which can be minimal amounts, since the accrual/index side effect doesn't depend on trade size), mirroring the "frequent reward updates" attack path in the reference report. The economic cost is the transaction/gas fee per call, and any small positive-EV incentive (e.g., depriving competing depositors of yield) makes it viable, exactly as debated for the Kwenta case.

### Recommendation
Decouple `last-update` from a single combined gate, or track fractional/unaccrued time explicitly:
- Update `last-update` unconditionally to `stacks-block-time` on every non-paused `accrue()` call (regardless of whether either index changed), so unresolved sub-threshold time deltas are never silently dropped and both indices always compute over the true elapsed time since the last block, or
- Maintain separate `last-update-borrow` / `last-update-liquidity` timestamps that only advance when their respective index changes, ensuring the liquidity index keeps accumulating the full elapsed time until it can register a change, or
- Increase `INDEX-PRECISION` and/or round the liquidity multiplier up to at least 1 unit above `INDEX-PRECISION` when accrual is non-zero in expectation, avoiding zero-delta rounding while a nonzero rate is active.

### Proof of Concept
1. Vault has nonzero utilization and reserve-factor such that `liquidity-rate < borrow-rate` (always true for reserve-factor > 0%).
2. Attacker calls `system-repay`/`system-borrow` with a minimal amount in consecutive blocks (or every few blocks), each of which invokes `accrue()`.
3. For each call, `time-delta` is small (e.g., 2-6 seconds). `next-index` rounds up: `INDEX-PRECISION + ceil(borrow-rate * time-delta * INDEX-PRECISION / SECONDS-PER-YEAR-BPS)` — a nonzero increment even for tiny `time-delta`, because of the `mul-div-up` rounding.
4. `next-liquidity-index` rounds down: `INDEX-PRECISION + floor(liquidity-rate * time-delta * INDEX-PRECISION / SECONDS-PER-YEAR-BPS)` — this remains equal to the current `lindex` (i.e., 0 increment) because `liquidity-rate` is scaled down by utilization and `(1 - reserve-factor)`.
5. Since `idx != next` (borrow side changed), the shared guard `(or (not (is-eq idx next)) (not (is-eq lidx nliq)))` is true, so `last-update` is reset to `stacks-block-time`.
6. Depositors' `lindex` never advances because every future `accrue()` call starts its `time-delta` calculation from the just-reset `last-update`, repeating the same rounding-to-zero outcome for the liquidity side indefinitely, while the debt side (`index`) keeps ticking up from borrowers.
7. Result: liquidity providers holding zUSDC accrue effectively zero yield for as long as the attacker sustains this call cadence, a permanent loss of their unclaimed interest for that period. [2](#0-1) [1](#0-0)

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L170-190)
```text
(define-private (calc-multiplier-delta (rate uint) (time-delta uint) (round-up bool))
  (+ INDEX-PRECISION
    (if round-up
      (mul-div-up rate
                  (* time-delta INDEX-PRECISION)
                  SECONDS-PER-YEAR-BPS)
      (mul-div-down rate
                  (* time-delta INDEX-PRECISION)
                  SECONDS-PER-YEAR-BPS))))

(define-private (calc-cumulative-debt (principal-amount uint) (idx uint))
  (mul-div-up principal-amount idx INDEX-PRECISION))

(define-private (calc-index-next (index-curr uint) (multiplier uint))
  (mul-div-down index-curr multiplier INDEX-PRECISION))

(define-private (calc-liquidity-rate (var-borrow-rate uint) (util-pct uint) (reserve-factor-bps uint))
  (let ((util-applied (mul-bps-down var-borrow-rate util-pct))
        (one-minus-rf (- BPS reserve-factor-bps)))
    (mul-bps-down util-applied one-minus-rf)))

```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L381-406)
```text
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

(define-private (principal-ratio-reduction (amount uint))
  (calc-principal-ratio-reduction amount (var-get principal-scaled) (debt-preview)))

```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L833-861)
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
