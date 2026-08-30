### Title
Interest Rate Applied to Entire Elapsed Period Instead of Time-Weighted Rate Path - (File: `mainnet/contracts/vault/v0-vault-stx.clar` and equivalent `v0-vault-*.clar` vaults)

### Summary
The vaults (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`) compute the borrow/liquidity index by taking a single point-in-time `interest-rate` snapshot and multiplying it by the entire elapsed time since `last-update`, exactly matching the "funding applied at peaks and troughs" bug class from the external report.

### Finding Description
`next-index` and `next-liquidity-index` compute `rate` from the current `interest-rate` (which is derived from current `utilization`), compute `time-delta` as the full elapsed time since `last-update`, and then apply that single current-rate value over the whole `time-delta` via `calc-multiplier-delta`: [1](#0-0) 

`calc-multiplier-delta` linearly scales the rate by `time-delta`, with no path-dependence — it assumes the rate held constant for the whole interval: [2](#0-1) 

`interest-rate` is derived purely from the current `utilization()` at call time, interpolated over rate curve points: [3](#0-2) 

Because `utilization` is `debt / (debt + available-liquidity)`, it can jump sharply within a single transaction (e.g., a large borrow that spikes utilization near the curve's steep/kink region, or a large deposit/withdrawal that suddenly changes `available-liquidity`). Since index updates only occur when a state-changing action (deposit/borrow/repay/withdraw) is triggered, if the vault goes untouched for a long `time-delta` and then someone (or a sequence of transactions) transiently pushes utilization to a rate peak right as the index update executes, that instantaneous peak rate gets retroactively applied to the entire elapsed period since `last-update`, not just the moment it occurred. This is the same mechanism as the reported `PerpAsset._updateFundingRate()` issue: a single last-observed rate multiplied by a stale elapsed-time window.

### Impact Explanation
This can cause the borrow index (and hence `total-debt`/`debt-preview`) or the liquidity index (share value) to jump by an amount disproportionate to actual time-weighted utilization, transferring value between borrowers and depositors incorrectly. For a long-idle vault (e.g., a less-active asset), an attacker who can influence `utilization` at the moment of index update (e.g., via a large borrow/deposit immediately before triggering accrual, then reversing it) could cause the index to overstate/understate interest owed for the whole idle period, resulting in mispriced shares (`convert-to-shares-preview`/`convert-to-assets-preview`) and potential temporary freezing/mispricing of depositor or borrower funds. This lands in the temporary-freezing-of-funds impact category since share/debt values become distorted relative to true accrual, though the report itself notes that in practice this design was accepted by the equivalent upstream team as tolerable for actively-traded assets and mitigated by "a regular settler/updater worker."

### Likelihood Explanation
Likelihood is limited by the same factors the original report calls out: vaults that are accessed frequently (deposit/borrow/repay/withdraw calls all trigger `next-index`/`next-liquidity-index`) will have small `time-delta` windows, making the effect negligible. The risk is elevated only for vaults with low activity (long gaps between calls) combined with an attacker's ability to manipulate `utilization` momentarily at the exact block the update fires — this requires being the party (or colluding within one transaction) that both changes utilization and triggers the index recalculation, which is feasible via a single transaction sequence (e.g., flash-borrow-then-accrue-then-repay is out of scope per rules, but a plain large borrow/repay in the same block that also happens to be the first vault touch after a long idle period is in scope).

### Recommendation
Use a piecewise/time-weighted accrual approach that checkpoints the rate more granularly (e.g., accrue on every block or use a cap on the maximum time-delta a single rate application), or run frequent keeper/automation calls to `next-index`/`next-liquidity-index` (as the equivalent report resolution suggests) to bound the maximum elapsed window between rate snapshots.

### Proof of Concept
1. Vault (e.g., `v0-vault-stx.clar`) sits idle for a long period; `last-update` is stale and utilization is moderate.
2. A user submits a large `borrow` that spikes `utilization()` sharply, pushing `interest-rate()` (via `interpolate-rate`) near a curve's high-slope segment: [3](#0-2) 
3. Within the same transaction/call path, the borrow logic triggers `next-index`/`next-liquidity-index`, which reads this newly spiked rate and multiplies it by the entire stale `time-delta` since `last-update`: [4](#0-3) 
4. The resulting `index`/`lindex` overstates accrued interest for the whole idle window, distorting `debt-preview`/`total-assets-preview` and thus share pricing for all depositors/borrowers, even though the elevated rate only existed for an instant. [5](#0-4)

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L369-390)
```text
  (calc-utilization (get-available-assets) (total-debt)))

(define-private (interest-rate)
  (let ((points-data (var-get points-ir))
        (uword (get util points-data))
        (rword (get rate points-data))
        (utils (unpack-u16 uword))
        (rates (unpack-u16 rword)))
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
                          (calc-multiplier-delta rate time-delta true))))
          (calc-index-next idx multiplier)))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L472-472)
```text
(define-read-only (get-last-update) (ok (var-get last-update)))
```
