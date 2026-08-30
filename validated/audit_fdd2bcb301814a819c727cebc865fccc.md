### Title
`accrue()` only advances `last-update` when the index actually changes, letting the pause-passthrough branch and small time-deltas silently freeze the interest clock - (File: `mainnet/contracts/vault/v0-vault-stx.clar`)

### Summary
The vault's `accrue` function only writes `last-update` to `stacks-block-time` when the newly computed `index`/`lindex` differ from the stored values. `next-index` derives the compounding period from `(- stacks-block-time (var-get last-update))`. Because `last-update` is a "clock advanced only on change," any interaction that leaves the index numerically unchanged (accrual paused, or a period whose computed multiplier rounds back to `INDEX-PRECISION`) leaves the stored `last-update` stale while `stacks-block-time` keeps moving forward.

### Finding Description
`accrue()` in `mainnet/contracts/vault/v0-vault-stx.clar` (and the equivalent code in every other vault: `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) implements: [1](#0-0) 

Two branches govern whether the accrual clock (`last-update`) moves:

1. **Pause pass-through**: when `(get accrue states)` is true, `accrue` returns the *current* `index`/`lindex` without touching `last-update` at all - it does not revert, it simply "passes through": [2](#0-1) 

2. **Conditional clock advance**: even when unpaused, `last-update` is written only `(if (or (not (is-eq idx next)) (not (is-eq lidx nliq))) (var-set last-update stacks-block-time) false)` - i.e., the clock advances only if the recomputed index actually changed: [3](#0-2) 

`next-index`, which is called on the *next* invocation of `accrue`, computes the elapsed period directly from this stale timestamp: [4](#0-3) 

Because `var-get last-update` was frozen while `accrue` was paused (or while the index computation rounded to no change), the very next successful `accrue()` call computes `time-delta` as the *entire* elapsed wall-clock span since the last real index write - not since the last call. `interest-rate()` at that moment reflects only the *current* utilization/rate point, so the whole frozen interval (which may have included very different utilization, or a period where accrual was intentionally paused) is compounded at a single point-in-time rate. This is the direct Clarity analogue of the "clock advanced only on change" pattern flagged in the TWAV report: a monotonic clock value is supposed to represent "time since last state update" but is only mutated conditionally, so any caller relying on `stacks-block-time - last-update` to represent a bounded, predictable period instead receives an unbounded, silently-accumulated span whose magnitude depends on how long the index happened not to change.

### Impact Explanation
`next-index`/`next-liquidity-index` feed directly into borrower debt (`total-debt`, `debt-preview`) and depositor share pricing (`convert-to-assets`, `convert-to-shares`) used throughout `deposit`, `redeem`, `system-borrow`, `system-repay`, and by `market.clar`'s health/liquidation checks via `accrue-and-cache`. Because the pause branch silently withholds `last-update` progression while continuing to allow `deposit`/`redeem`/`borrow`/`repay` to be gated only by their own per-action pause flags (not by `accrue`'s pause flag), a long pause of the `accrue` action followed by unpausing forces the very next transaction to compound the *entire* frozen interval's rate onto the index in one step. This can materially misprice zToken shares and outstanding debt for all users in a single block - a "temporary freezing/mispricing of funds" style impact (accrual is either withheld from suppliers, or a single unlucky caller pays/receives a disproportionate index jump), consistent with the Medium classification the original report received for its own single-block clock-mishandling defect.

### Likelihood Explanation
This does not require any external condition or privileged action beyond normal DAO-controlled pause/unpause of the `accrue` flag (an operational lever explicitly supported by `pause-states`), or simply a stretch of blocks where utilization/rate happens to make the multiplier round to `INDEX-PRECISION`. Both paths are reachable in ordinary operation without any DAO misconfiguration or oracle manipulation, so likelihood is non-trivial, though it requires a specific but plausible sequence (pause accrue → time passes → unpause → any lending call).

### Recommendation
Decouple "time since last check" from "index changed": always update `last-update` to `stacks-block-time` at the end of every successful (non-paused) `accrue()` call, regardless of whether `index`/`lindex` numerically changed, so `time-delta` in `next-index` never silently accumulates a hidden compounding window. Separately, ensure that when `accrue` is paused, all dependent lending/deposit/redeem paths are also paused (or `last-update` is reset to `stacks-block-time` at unpause time) so that no interest is retroactively compounded across the paused interval.

### Proof of Concept
1. Vault is operating normally; `last-update` reflects the last block where `index` changed.
2. DAO (or authorized pauser) sets `pause-states.accrue = true` via governance action already exposed by the contract.
3. Many blocks pass; `stacks-block-time` advances, but every call to `accrue()` hits the pass-through branch at [2](#0-1)  and returns the stale `index`/`lindex` without writing `last-update`.
4. DAO unpauses `accrue`.
5. Any user calls `deposit`, `redeem`, `system-borrow`, or `system-repay`, each of which calls `(try! (accrue))` first (e.g., [5](#0-4) ).
6. `next-index` computes `time-delta = stacks-block-time - last-update` using the pre-pause `last-update`, so the entire pause duration is compounded at the *current* single-point interest rate in one step [6](#0-5) , producing an index jump that does not reflect the actual interest-rate history during the paused window, mispricing shares/debt for every position in the vault at that moment.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L377-388)
```text
    (interpolate-rate (utilization) utils rates)))

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
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L763-766)
```text
(define-public (deposit (amount uint) (min-out uint) (recipient principal))
    (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L835-863)
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
