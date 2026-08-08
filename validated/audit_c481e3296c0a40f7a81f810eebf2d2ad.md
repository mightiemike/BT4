### Title
Chained integer divisions in `throttling_wait_ms_internal` cause silent loss of index-flush throttling, leading to disproportionate CPU/disk-IOPS cost - (File: accounts-db/src/accounts_index/bucket_map_holder.rs)

### Summary
`BucketMapHolder::throttling_wait_ms_internal` computes an estimated flush rate and then reuses that (already rounded) intermediate value in a second division to project future progress. Because both divisions are unsigned integer divisions, the result is not equivalent to a single, more precise division, and truncation in the first division can zero out the second calculation, silently disabling the throttling mechanism whose entire purpose is to prevent CPU/disk I/O spikes during accounts-index bucket flushing.

### Finding Description
`throttling_wait_ms_internal` is meant to spread the periodic flushing of `AccountsIndex` bins to disk evenly across `age_interval_ms` (`400` ms in `Threshold` mode, `2000` ms in `Minimal`/disk-index mode), specifically to "avoid cpu spikes at beginning of age interval" per its own doc comment: [1](#0-0) 

The function performs two chained integer divisions instead of one combined division: [2](#0-1) 

```rust
let ms_per_s = 1_000;
let rate_bins_per_s = bins_flushed * ms_per_s / elapsed_ms;
let expected_bins_processed_in_remaining_time = rate_bins_per_s * remaining_ms / ms_per_s;
if expected_bins_processed_in_remaining_time > remaining_bins {
    Some(1)
} else {
    None
}
```

Mathematically this is intended to approximate `bins_flushed * remaining_ms / elapsed_ms`, but performing it as two separate truncating integer divisions (first `/elapsed_ms`, then `/ms_per_s`) introduces avoidable rounding error — exactly the bug class described in the reference report ("Avoid multiple divisions... which can be reduced to one... to avoid any rounding errors"). Here the effect is worse than a simple precision loss: whenever `bins_flushed * ms_per_s < elapsed_ms` (i.e., the observed flush rate is less than 1 bin per second, which is entirely plausible early in an age interval or under disk contention/slow I/O), `rate_bins_per_s` truncates to `0`. Once that intermediate value is `0`, the second division `0 * remaining_ms / ms_per_s` is always `0`, so `expected_bins_processed_in_remaining_time` can never exceed `remaining_bins`, and the function returns `None` (do not wait) even though the true combined ratio `bins_flushed * remaining_ms / elapsed_ms` would show the flush is running far ahead of/behind schedule and pacing is warranted.

By contrast, computing the single combined ratio `bins_flushed.saturating_mul(remaining_ms) / elapsed_ms.max(1)` preserves precision (all values fit well within `u64`, since `bins` and elapsed/remaining ms are all small), fully mirroring the recommended fix pattern in the reference report of collapsing multiple divisions into a single division on precomputed numerator/denominator terms.

### Impact Explanation
This function directly gates `AccountsIndexStorage`'s background eviction/flush thread's self-throttling for the disk-backed account index (`IndexLimit::Minimal` / `IndexLimit::Threshold`), used when `--accounts-index-path`/disk index or a memory threshold is configured. When the throttle incorrectly never fires due to the truncation-to-zero described above, the flush loop no longer paces itself and can burst through all remaining bins at full speed rather than spreading the work over the configured interval, causing the exact "disproportionate storage and CPU cost" class of impact accepted for this scan (I/O and CPU spikes on validators using the disk-based accounts index or a memory threshold, rather than the default in-memory index). This is a purely mechanical, deterministic rounding defect (not a maliciously crafted input) reachable on any unprivileged node running with these config options, since flush timing/rate naturally varies with I/O latency and workload.

### Likelihood Explanation
The zeroing condition (`bins_flushed * 1000 < elapsed_ms`) occurs whenever the observed per-tick flush rate drops below one bin per second — a very ordinary and frequent condition, e.g., near the start of an age interval when only 1-2 bins have flushed and a few hundred ms have elapsed, or under any disk latency/backpressure. Because the affected configuration (disk/threshold index) is a supported, non-default but real production option for large validators using `--accounts-index-path` or a memory threshold, and the miscalculation requires no attacker action, likelihood of it manifesting during ordinary operation is high, though its consequence is a performance/pacing regression rather than consensus-affecting state corruption.

### Recommendation
Replace the two chained divisions with a single division using the un-rounded product, e.g.:
```rust
let expected_bins_processed_in_remaining_time =
    bins_flushed.saturating_mul(remaining_ms) / elapsed_ms.max(1);
```
removing the intermediate `rate_bins_per_s` computation and its associated `ms_per_s` division entirely, so that no truncation-to-zero can occur before the comparison against `remaining_bins`.

### Proof of Concept
Given `bins_flushed = 1`, `elapsed_ms = 2000` (i.e., flush rate is 0.5 bins/sec), and `remaining_ms = 100_000` (a large remaining window), `interval_ms` large enough that `remaining_bins > 0`:
- Current code: `rate_bins_per_s = 1 * 1000 / 2000 = 0` → `expected_bins_processed_in_remaining_time = 0 * 100_000 / 1000 = 0`, which is never `> remaining_bins`, so the function always returns `None` (no throttling), regardless of how far behind/ahead the true rate would put it.
- Combined single-division form: `expected = 1 * 100_000 / 2000 = 50`, correctly reflecting that 50 bins are expected to be processed in the remaining time, which can then be correctly compared against `remaining_bins` to decide whether to throttle.

This can be directly reproduced by unit-testing `throttling_wait_ms_internal` with the values above and observing it never returns `Some(1)` under the buggy formula while the corrected single-division formula does, for inputs where throttling should occur.

### Citations

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L412-436)
```rust
    /// return an amount of ms to sleep
    fn throttling_wait_ms_internal(
        &self,
        interval_ms: u64,
        elapsed_ms: u64,
        bins_flushed: u64,
    ) -> Option<u64> {
        let target_percent = 90; // aim to finish in 90% of the allocated time
        let remaining_ms = (interval_ms * target_percent / 100).saturating_sub(elapsed_ms);
        let remaining_bins = (self.bins as u64).saturating_sub(bins_flushed);
        if remaining_bins == 0 || remaining_ms == 0 || elapsed_ms == 0 || bins_flushed == 0 {
            // any of these conditions result in 'do not wait due to progress'
            return None;
        }
        let ms_per_s = 1_000;
        let rate_bins_per_s = bins_flushed * ms_per_s / elapsed_ms;
        let expected_bins_processed_in_remaining_time = rate_bins_per_s * remaining_ms / ms_per_s;
        if expected_bins_processed_in_remaining_time > remaining_bins {
            // wait because we predict will finish prior to target
            Some(1)
        } else {
            // do not wait because we predict will finish after target
            None
        }
    }
```

**File:** accounts-db/src/accounts_index/bucket_map_holder.rs (L438-441)
```rust
    /// Check progress this age.
    /// Return ms to wait to get closer to the wait target and spread out work over the entire age interval.
    /// Goal is to avoid cpu spikes at beginning of age interval.
    fn throttling_wait_ms(&self) -> Option<u64> {
```
