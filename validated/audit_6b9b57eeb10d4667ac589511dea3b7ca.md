### Title
`set-pause-states` jumps `last-update` on unpause, permanently skipping interest/points accrual for the entire paused duration - ([File: mainnet/contracts/vault/v0-vault-usdc.clar])

### Summary
Every vault contract (`v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-sbtc.clar`, `v0-vault-stx.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`) mitigates a stale-rate accrual issue the same way the Reserve Protocol `Furnace` did: instead of reverting when accrual is paused, `accrue` passes through silently, and when the DAO unpauses, `last-update` is jumped forward to the current time, silently discarding all interest that should have accrued to depositors/lenders for the paused period.

### Finding Description
`accrue` reads `pause-states` and, when `accrue` is paused, returns the *current* index/lindex unchanged instead of reverting: [1](#0-0) 

The index math itself is driven by `time-delta = stacks-block-time - last-update`: [2](#0-1) 

`set-pause-states` explicitly documents and implements the mitigation: on pause it accrues once to capture pending interest, but on *unpause* it jumps `last-update` straight to `stacks-block-time`, deliberately "skipping" the paused window rather than preserving it for later distribution: [3](#0-2) 

This is structurally identical to the reported Furnace bug: the original bug was that a stale rate could apply retroactively to a period it shouldn't; the "fix" avoids that by disabling accrual during the paused window and then re-anchoring the checkpoint (`last-update`) to `now`, which erases the entitlement to interest for that whole window instead of preserving/replaying it. The `index`/`lindex` value that determines depositor/borrower interest is the value bound at pause time, invalidated in meaning (but not value) by the unrelated event of unpausing, and later used by `next-index`/`next-liquidity-index` as if no time had elapsed during the pause.

### Impact Explanation
Lenders (zft holders, who accrue yield via `lindex`) and the protocol's fee-reserve/treasury LP mint (tied to `index` growth) permanently lose all interest/points accrual for the pause duration. This is not a case of double distribution risk being avoided at zero cost — it is a permanent loss of unclaimed yield that depositors and the treasury would otherwise have earned, matching the in-scope impact class "permanent freezing of unclaimed yield" — no later action can recover the interest that would have accrued for that skipped interval, since `last-update` has already been moved forward and the elapsed time is unrecoverable.

Borrowers are also affected (their debt would have grown less "correctly" for a shorter time but the interest still isn't compounded for the skipped window), but the clearer loss is on the lender/treasury side, since that yield is simply erased rather than being paid at a possibly wrong rate.

### Likelihood Explanation
Likelihood is moderate to high in ordinary DAO operations: pausing `accrue` for maintenance, migrations, oracle/rate updates, or incident response is a normal governance action, and every unpause event of this pause flag deterministically triggers the loss — no attacker action or malicious interleaving is required beyond the DAO calling `set-pause-states` twice (pause, then unpause) as intended. The longer the pause window, the larger the amount of interest/points permanently lost.

### Recommendation
On unpause, do not simply reset `last-update` to `stacks-block-time`. Instead:
- Either keep `last-update` unchanged so the next `accrue` call correctly folds in the entire elapsed period (pause + post-pause) using the *current* rate at time of unpause (consistent handling, no loss), or
- Explicitly compute and credit the pending interest for the paused window at the rate frozen at pause time before advancing `last-update`, so depositors/treasury still receive what was already accruing prior to the pause.
The key fix is to make sure the checkpoint update and the value it protects move together — never advance `last-update` without also crediting (or intentionally, and documented, forgoing with DAO/governance sign-off) the interest for the time it represents.

### Proof of Concept
1. DAO calls `accrue` implicitly via any operation; `index`/`lindex`/`last-update` reflect state at block `T0`.
2. DAO calls `set-pause-states` setting `accrue: true`. Per [4](#0-3) , `accrue` is called once more to flush pending interest up to `T0`, then `pause-states` is updated.
3. Time passes; interest should be accruing on outstanding debt at `interest-rate()` for `T1 - T0` (`T1` = time of unpause), but because `accrue states -> accrue: true`, every call to `accrue` during this window returns the frozen `index`/`lindex` unchanged ( [5](#0-4) ).
4. At `T1`, DAO calls `set-pause-states` again setting `accrue: false`. Because `was-paused` is `true` and `now-paused` is `false`, the branch at [6](#0-5)  executes: `(var-set last-update stacks-block-time)`, setting `last-update = T1`.
5. Any subsequent `accrue` call computes `time-delta = stacks-block-time - last-update`, which starts counting from `T1`, not `T0`. The interval `[T0, T1]` — during which real-world debt was still outstanding and would have been accruing interest under normal (non-paused) protocol design — is permanently excluded from the index calculation in `next-index`/`next-liquidity-index` ( [2](#0-1) ).
6. Result: lenders (zft holders) and the treasury (via `treasury-lp` minted proportional to `debt-delta`) permanently lose the interest/fees that should have compounded for `[T0, T1]`. No later transaction can recover this value, since the state that tracked it (`last-update`) has already been advanced past it.

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L837-845)
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
```

**File:** local-testing/contracts/vault/vault-sbtc.clar (L381-406)
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

**File:** local-testing/contracts/vault/vault-sbtc.clar (L725-739)
```text
(define-public (set-pause-states (states {deposit: bool, redeem: bool, borrow: bool, repay: bool, accrue: bool, flashloan: bool}))
  (begin
    (try! (check-dao-auth))
    (let ((current (var-get pause-states))
          (was-paused (get accrue current))
          (now-paused (get accrue states)))
      ;; When pausing accrue, accrue first to capture pending interest
      (if (and (not was-paused) now-paused)
          (begin (try! (accrue)) false)
          false)
      ;; When unpausing accrue, jump last-update to now to skip paused period
      (if (and was-paused (not now-paused))
          (var-set last-update stacks-block-time)
          false)
      (var-set pause-states states)
```
