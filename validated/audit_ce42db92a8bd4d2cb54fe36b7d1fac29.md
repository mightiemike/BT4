### Title
Interest accrual timestamp (`last-update`) is only advanced when the index changes, letting a dormant zero-rate period be re-priced at a later non-zero rate in a single transaction - ([File: mainnet/contracts/vault/v0-vault-stx.clar])

### Summary
The vault's interest-accrual clock (`last-update`) is a "clock advanced only on change" pattern: it is only updated inside `accrue` when the freshly computed `index`/`lindex` differ from the stored ones. When the interpolated borrow rate is `0` (a valid, DAO-configured point on the utilization/rate curve, e.g. base rate at 0% utilization), every call to `accrue` computes `next == idx` and `nliq == lindex`, so `last-update` is never advanced even though real time is passing. Once utilization moves off that zero-rate breakpoint, the very next `accrue` call (triggered atomically by the same `borrow`/`repay`/`deposit` transaction that changed utilization) computes `time-delta = stacks-block-time - last-update` spanning the *entire* dormant zero-rate window, and applies the newly non-zero rate over that whole span in one shot.

### Finding Description
`next-index` and `next-liquidity-index` compute the multiplier from `(- stacks-block-time (var-get last-update))`: [1](#0-0) 

`accrue` only commits the new indexes and — critically — only advances `last-update` when at least one of the indexes actually changed: [2](#0-1) 

If the interpolated `interest-rate()` is `0` (a legitimate curve breakpoint, not a misconfiguration or DAO compromise), `calc-multiplier-delta` returns `INDEX-PRECISION` unchanged, so `next == idx` and `nliq == lindex`. Consequently the guard `(or (not (is-eq idx next)) (not (is-eq lidx nliq)))` is false and `last-update` is **not** bumped to `stacks-block-time`, even though the block timestamp has clearly advanced. This is exactly the reported bug class: a value (`last-update`, analogous to Presale's `openDate`) is meant to gate/anchor time-based logic, but a later state-changing call (`system-borrow`/`system-repay`/`deposit`, analogous to `open()`) can be evaluated using a stale anchor that was never invalidated, letting a much larger effective time window be applied than intended.

Sequence:
1. Vault's `points-rate` curve is set (via normal DAO configuration) so the rate at low/zero utilization is `0`.
2. At time `T0`, utilization is `0` (no outstanding debt); `accrue()` runs, `index=I0`, `lindex=L0`, `last-update=T0`. Rate is `0`, so on every subsequent call `next==idx` and `last-update` stays `T0`.
3. Time passes to `T1 >> T0` with no debt (rate stays `0`); each incidental `accrue()` call (from deposits/redeems) leaves `last-update` pinned at `T0`.
4. At `T1`, any borrower calls `system-borrow`, pushing utilization above the zero-rate breakpoint so `interest-rate()` now returns `R > 0`.
5. `system-borrow` calls `accrue()` first (same transaction): `time-delta = T1 - last-update = T1 - T0`, i.e., the entire dormant span, is multiplied by the now-nonzero rate `R` via `calc-multiplier-delta`.
6. `index` and `lindex` both jump by a multiplier reflecting rate `R` compounded over the *whole* dormant period, not just the instant since utilization changed. This mints an outsized `treasury-lp` amount and inflates `lindex` (the exchange rate for all zToken holders) in a single block, and inflates the borrower's own freshly-created debt via the same `index` jump.

### Impact Explanation
The mispriced index jump is applied atomically within the triggering transaction, misallocating value between existing zToken holders, the newly-borrowing user, and the DAO treasury (`treasury-lp` minted from `debt-delta * fee-reserve`) based on a rate that was not actually in effect during most of the elapsed period. This can inflate `lindex` (liquidity index) disproportionately, effectively minting yield for existing suppliers that was never earned, and/or overcharge the interacting borrower's own debt index within one transaction. Repeated/engineered dormancy-then-activation cycles across a vault's lifetime could produce protocol-level index drift, i.e., insolvency risk between total minted zToken value and real underlying assets. This lands in **temporary freezing of funds / theft of unclaimed yield**, and if compounded across cycles, threatens vault solvency (Critical).

### Likelihood Explanation
This requires only that a vault's configured `points-rate` curve include a `0` rate at some utilization breakpoint (a normal curve shape choice, not a misconfiguration or DAO compromise) and that utilization dwell at that breakpoint for a period before crossing it — both realistic, unprivileged conditions triggerable by any user via ordinary `deposit`/`system-borrow`/`system-repay` calls.

### Recommendation
Always advance `last-update` to `stacks-block-time` on every `accrue()` call regardless of whether the computed index changed, so `time-delta` never spans a period during which the rate assumption used for repricing was not the rate actually in effect throughout that span. Mirror the fix pattern used for the presale bug: don't let a state anchor persist unchanged simply because its dependent output didn't change — the anchor must track real elapsed time, not derived-value equality.

### Proof of Concept
Not independently executed in this session (no test/terminal access); the trace above is derived directly from `next-index`/`next-liquidity-index`/`accrue` in `mainnet/contracts/vault/v0-vault-stx.clar` [1](#0-0) [2](#0-1) . The same `last-update`/`accrue` pattern is repeated in the other vault contracts (`v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`), each with matching `stacks-block-time` usage per the earlier grep results, so this same reproduction path applies to all of them. A concrete numeric PoC (deploying to a local Clarinet/simnet environment, setting a curve with a `0` base rate, advancing block time, then borrowing) would require terminal/test-runner access, which was not available in this session — a Devin agent with repo + test-harness access should build and run this PoC to confirm the exact numeric index inflation.

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
