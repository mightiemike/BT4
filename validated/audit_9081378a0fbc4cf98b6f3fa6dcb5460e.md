### Title
Interest-rate clock only advances when the index actually changes, letting a dormant-then-reactivated vault retroactively apply the wrong rate over the frozen period - (File: mainnet/contracts/vault/v0-vault-stx.clar)

### Summary
`accrue()` only updates `last-update` when `index`/`lindex` actually move. When the interest rate is exactly `0` (e.g., zero utilization), `next-index`/`next-liquidity-index` return the unchanged index, so `last-update` is *not* advanced even though real time has elapsed. When utilization later rises and the rate becomes non-zero, the very next `accrue()` computes `time-delta = stacks-block-time - last-update` spanning the *entire* dormant period, and applies the *current* (post-change) rate over that whole span instead of the (near-zero) rate that actually applied throughout most of it. This causes a single-block, oversized index jump that mis-prices interest for the whole idle interval.

### Finding Description
`accrue()` binds `next`/`nliq` from `next-index`/`next-liquidity-index`, mutates `index`/`lindex` only if changed, mints treasury shares based on `debt-delta`, and finally updates the clock only conditionally: [1](#0-0) 

`next-index`/`next-liquidity-index` compute `time-delta` from the stored `last-update`, and the multiplier is `INDEX-PRECISION` (i.e., no change) whenever `interest-rate` resolves to `0`: [2](#0-1) 

Sequence:
1. Vault utilization drops to a point on the IR curve where `interest-rate` returns `0` (e.g., all debt repaid, 0% utilization).
2. Every subsequent `accrue()` call computes `next == idx` and `nliq == lidx`, so the `(if (or (not (is-eq idx next)) ...) (var-set last-update stacks-block-time) false)` branch is never taken — `last-update` is frozen at the timestamp of the last real index change (T0), even as real time (and blocks) pass.
3. Later, a borrow pushes utilization up, making `interest-rate` non-zero.
4. The next `accrue()` call computes `time-delta = stacks-block-time - last-update`, which now spans the *entire dormant interval* (T0 to now), and multiplies it by the *current*, non-zero (potentially high-utilization) rate via `calc-multiplier-delta`.
5. This produces one large `next`/`nliq` jump in a single accrual call, charging every existing borrower interest for the dormant period at a rate that never actually applied during that period, and crediting suppliers'/treasury's share price accordingly in the same transaction.

This matches the "clock advanced only on change" analog class: a timestamp/clock variable that should track elapsed time is instead gated on a value actually differing, letting elapsed real time silently accumulate unaccounted, then get misapplied at the wrong rate in one shot.

### Impact Explanation
The bug misprices interest across a period retroactively at the wrong rate, transferring value between suppliers/treasury and borrowers. An attacker can deposit into the vault while it is dormant (rate ≈ 0), wait, then trigger a borrow that pushes utilization to a high-rate region, forcing the very next `accrue()` to backdate the high rate across the whole dormant window in a single block, inflating the liquidity index (and thus the attacker's zToken value) disproportionately relative to the interest that genuinely accrued. This effectively lets an attacker capture unclaimed yield that should never have accrued (paid for by borrowers whose debt is inflated for a period during which the real rate was ~0%), landing in the "temporary freezing of funds" / "theft of unclaimed yield" impact category since borrower debt increases beyond what actually accrued and depositors captured that windfall in one transaction.

### Likelihood Explanation
This requires a vault to reach a zero (or point-of-curve-tie) interest rate for a sustained period (fully repaid vault, or an IR curve segment with a flat/zero point), and then have utilization actively pushed up by any borrower (not necessarily the attacker) to trigger the reactivation accrual. All state transitions are single-transaction/single-block operations reachable through the normal public `borrow`/`system-borrow`/`accrue` entry points with no privileged access needed, but it depends on IR-curve configuration exposing a true zero-rate region, which is a DAO-configurable parameter — so the actual likelihood is contingent on the deployed curve shape at the relevant vaults.

### Recommendation
Always update `last-update` to `stacks-block-time` whenever `accrue()` runs the "not paused" branch, regardless of whether `index`/`lindex` changed, so `time-delta` for the next accrual is always measured from the last accrual attempt rather than the last actual index change.

### Proof of Concept
1. Vault `v0-vault-stx` reaches 0% utilization → `interest-rate` returns `0`; `last-update` = T0 after the last real accrual.
2. Wait N blocks/seconds with utilization at 0% (no accruing borrow activity); `last-update` remains T0 because `next == idx` on every `accrue()` call (per `mainnet/contracts/vault/v0-vault-stx.clar:851-862`).
3. Attacker deposits liquidity (mints zSTX at current share price).
4. Attacker (or anyone) calls `borrow`/`system-borrow` sufficient to move utilization into a high-rate zone of the IR curve.
5. The triggered `accrue()` call now computes `time-delta = stacks-block-time - T0` (the full dormant span) and applies the newly high `interest-rate` over that entire span via `calc-multiplier-delta`, producing an oversized one-block jump in `index`/`lindex` (per `mainnet/contracts/vault/v0-vault-stx.clar:843-863`).
6. Attacker redeems zSTX immediately, capturing the inflated liquidity-index value — yield that was never actually earned at the real (near-zero) historical rate, funded by borrowers' inflated debt.

Note: I was unable to fully inspect `interpolate-rate`/`calc-multiplier-delta`/the deployed IR curve points within the available iterations, so I cannot confirm from the index alone that a genuine zero-rate point exists on the currently configured curve for each vault; this should be verified against the live IR curve configuration (`points-ir`) before treating this as confirmed-exploitable on a specific vault instance.

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

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L843-862)
```text
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
```
