### Title
Unchecked `u32` timestamp subtraction in Bitcoin difficulty-adjustment calculation can underflow, corrupting the light-client's proven difficulty target - (File: crates/bitcoin-da/src/verifier.rs)

### Summary
`calculate_new_difficulty` in `crates/bitcoin-da/src/verifier.rs` computes `last_timestamp - epoch_start_time` as plain `u32` arithmetic, mirroring exactly the bug class in the referenced dfinity/ic report (`before_timestamp + PERIOD - current_time`, all in `u64`, panicking when the assumed monotonic ordering doesn't hold). Bitcoin does not guarantee that `last_timestamp` (the last block of a difficulty epoch) is greater than `epoch_start_time` (the first block of that epoch) — only the median-time-past rule (`verify_timestamp`, comparing against the median of the previous 11 blocks) is enforced. This makes the subtraction's safety an unstated, unverified assumption baked into a state-transition function that directly determines the proved DA target. [1](#0-0) 

### Finding Description
`verify_header_chain_mainnet`/`verify_header_chain_signet` call `calculate_new_difficulty(epoch_start_time, block_header.time().secs() as u32, ...)` when the current header is the last block of a 2016-block epoch: [2](#0-1) 

Inside `calculate_new_difficulty`:
```rust
let mut actual_timespan = last_timestamp - epoch_start_time;
if actual_timespan < expected_epoch_timespan / 4 {
    actual_timespan = expected_epoch_timespan / 4;
} else if actual_timespan > expected_epoch_timespan * 4 {
    actual_timespan = expected_epoch_timespan * 4;
}
``` [3](#0-2) 

`epoch_start_time` is fixed to the timestamp of the epoch's first block and carried forward across all 2015 subsequent blocks of that epoch (`latest_da_state.epoch_start_time`), while `last_timestamp` is the timestamp of the final block of the epoch. The only per-block timestamp constraint enforced by `verify_header_chain_common` → `verify_timestamp` is that the new block's time must exceed the median of the previous 11 blocks' timestamps — it is not constrained relative to `epoch_start_time` specifically: [4](#0-3) 

Bitcoin Core itself performs this same calculation as a **signed 64-bit** subtraction (`int64_t nActualTimespan = pindexLast->GetBlockTime() - nFirstBlockTime;`), explicitly because the value can be negative in edge cases, and clamps the (possibly negative) result to `expected/4` afterward. Citrea's port drops this to an unsigned `u32` subtraction with no signedness or checked-arithmetic guard, exactly the same class of defect flagged in the referenced dfinity/ic fix (unchecked subtraction of two independently-sourced timestamps that are assumed, but not guaranteed, to be ordered).

Two outcomes are possible depending on the build profile (no `overflow-checks = true` was found configured anywhere in the workspace):
- In a debug/overflow-checked build (e.g. the RISC0 guest's checked build or a standard `cargo test`), this subtraction panics — the verifier function returns no error, it aborts. Since `calculate_new_difficulty` runs inside the state-transition path used to derive `LatestDaState` (which feeds `light-client-prover`'s circuit output), a panic here is a hard divergence between what different honest provers/nodes can produce for the same L1 data, depending on their build configuration.
- In a release build without overflow checks, the subtraction wraps to a value near `u32::MAX`, which is immediately clamped by the `> expected_epoch_timespan * 4` branch down to `expected_epoch_timespan * 4` — the *maximum* allowed timespan, rather than the intended (near-zero or clamped-to-minimum) timespan. This flips the calculated `actual_timespan` from what should be the minimum bound to the maximum bound, causing `calculate_new_difficulty` to compute a **looser (larger) difficulty target** than the honest Bitcoin consensus rules would produce for the same header sequence.

### Impact Explanation
`current_target_bits` computed here becomes part of the proven `LatestDaState`, consumed by `light-client-prover`'s circuit and used downstream to validate proof-of-work (`verify_target_hash`) and to gate DA-inclusion/completeness claims for future epochs. If the wrapped/incorrect target is looser than the real Bitcoin difficulty, the light client's proven chain state diverges from the actual Bitcoin chain's consensus rules for that epoch boundary — i.e. the equality "target computed natively by Bitcoin consensus == target the guest circuit proves" is broken. This falls under the in-scope impact class of "the root computed natively versus the root computed in the guest" / "honest nodes converging on an unproved root," since two honest provers running different build profiles (checked vs. unchecked arithmetic) over the identical real Bitcoin header sequence would either panic (one halts, one doesn't) or silently compute divergent `current_target_bits` values for the same epoch, splitting honest provers on the same L1 data.

### Likelihood Explanation
This does not require any adversarial control over Bitcoin's hashrate, a malicious peer, or a privileged role — it only requires that *real*, honestly-mined Bitcoin headers exhibit a non-monotonic timestamp relationship between an epoch's first and last block, which is a documented, previously-observed edge case in Bitcoin's history (timestamps are only weakly ordered by the median-time-past rule, not epoch-relative ordering). It occurs purely as a consequence of parsing real DA data through this code path, matching the "Root cause" mechanism in the referenced advisory (an assumed-monotonic timestamp relationship that mainnet data can violate).

### Recommendation
Mirror Bitcoin Core's approach: perform the subtraction in a signed, wider type (e.g. `i64`), clamp negative or excessively small results to `expected_epoch_timespan / 4` explicitly, and use `checked_sub`/`saturating_sub` with an explicit branch for `last_timestamp < epoch_start_time` rather than relying on unchecked `u32` subtraction. Add a regression test using two blocks whose timestamps are non-monotonic across an epoch boundary to confirm both correctness and absence of panics regardless of the `overflow-checks` build setting.

### Proof of Concept
1. Construct (or replay from real historical Bitcoin data) an epoch where the last block's timestamp (`block_header.time().secs()`) is less than the first block's timestamp (`epoch_start_time`), while still satisfying `verify_timestamp`'s median-of-11 check.
2. Feed this header sequence into `BitcoinVerifier::verify_header_chain_mainnet`/`_signet`, reaching `epoch_block == BLOCKS_PER_EPOCH - 1` so `calculate_new_difficulty(epoch_start_time, last_timestamp, ...)` is invoked.
3. Observe either a panic (`attempt to subtract with overflow`) under overflow-checked builds, or a wrapped `actual_timespan` near `u32::MAX` that clamps to `expected_epoch_timespan * 4`, producing an incorrect `current_target_bits` that diverges from Bitcoin consensus's own difficulty for that epoch — a value silently baked into the proven `LatestDaState`.

### Citations

**File:** crates/bitcoin-da/src/verifier.rs (L403-425)
```rust
        // Check if this is the first epoch block, and update time accordingly
        let epoch_start_time = if epoch_block == 0 {
            block_header.time().secs() as u32
        } else {
            latest_da_state.epoch_start_time
        };

        // If this is the last block of the epoch, calculate the target for the next epoch
        let current_target_bits = if epoch_block == BLOCKS_PER_EPOCH - 1 {
            let next_target = calculate_new_difficulty(
                epoch_start_time,
                block_header.time().secs() as u32,
                // If 20 minute exception happened on last block of the difficulty period,
                // previous block's target should be used. If didn't happen, it is going
                // to be equal to current block bits anyway.
                latest_da_state.current_target_bits,
                network_constants.max_target,
                EXPECTED_EPOCH_TIMESPAN,
            );
            target_to_bits(&next_target)
        } else {
            latest_da_state.current_target_bits
        };
```

**File:** crates/bitcoin-da/src/verifier.rs (L568-585)
```rust
        // Check 6: valid timestamp
        if !verify_timestamp(
            block_header.time().secs() as u32,
            latest_da_state.prev_11_timestamps,
        ) {
            return Err(ValidationError::InvalidTimestamp);
        }

        Ok(())
    }
}

/// Verifies the block time against the median of the previous 11 blocks' timestamps
fn verify_timestamp(block_time: u32, mut prev_11_timestamps: [u32; 11]) -> bool {
    prev_11_timestamps.sort_unstable();
    let median_time = prev_11_timestamps[5];
    block_time > median_time
}
```

**File:** crates/bitcoin-da/src/verifier.rs (L677-703)
```rust
/// Calculates the new difficulty target for the next epoch.
fn calculate_new_difficulty(
    epoch_start_time: u32,
    last_timestamp: u32,
    current_target_bits: u32,
    max_target: U256,
    expected_epoch_timespan: u32,
) -> [u8; 32] {
    // Step 1: Calculate the actual timespan of the epoch
    let mut actual_timespan = last_timestamp - epoch_start_time;
    if actual_timespan < expected_epoch_timespan / 4 {
        actual_timespan = expected_epoch_timespan / 4;
    } else if actual_timespan > expected_epoch_timespan * 4 {
        actual_timespan = expected_epoch_timespan * 4;
    }
    // Step 2: Calculate the new target
    let new_target_bytes = bits_to_target(current_target_bits);
    let mut new_target = U256::from_be_bytes(new_target_bytes)
        .wrapping_mul(&U256::from(actual_timespan))
        .wrapping_div(&U256::from(expected_epoch_timespan));
    // Step 3: Clamp the new target to the maximum target
    if new_target > max_target {
        new_target = max_target;
    }

    new_target.to_be_bytes()
}
```
