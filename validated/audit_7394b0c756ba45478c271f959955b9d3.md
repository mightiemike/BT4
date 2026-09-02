### Title
Difficulty-retarget underflow lets a single block's timestamp force an incorrectly easy PoW target in the next epoch - (File: `crates/bitcoin-da/src/verifier.rs`)

### Summary
`calculate_new_difficulty` computes the actual timespan of a Bitcoin difficulty epoch with an unsigned subtraction, `last_timestamp - epoch_start_time`, on `u32` values [1](#0-0) . Because Citrea's `verify_timestamp` check only requires a block's time to exceed the *median* of the previous 11 timestamps — not to be greater than the epoch-start timestamp — the last block of an epoch can legitimately carry a timestamp earlier than the epoch's first block [2](#0-1) . When that happens, `last_timestamp - epoch_start_time` underflows and wraps to a value near `u32::MAX` (the code base has no `overflow-checks = true` override, so release builds wrap silently instead of panicking).

### Finding Description
`verify_header_chain_mainnet`/`_testnet4`/`_signet` call `calculate_new_difficulty(epoch_start_time, block_header.time(), ..., EXPECTED_EPOCH_TIMESPAN)` exactly on the last block of each 2016-block epoch [3](#0-2) . Inside `calculate_new_difficulty`:
```
let mut actual_timespan = last_timestamp - epoch_start_time;
if actual_timespan < expected_epoch_timespan / 4 {
    actual_timespan = expected_epoch_timespan / 4;
} else if actual_timespan > expected_epoch_timespan * 4 {
    actual_timespan = expected_epoch_timespan * 4;
}
``` [4](#0-3) 

If `last_timestamp < epoch_start_time` (allowed by the header-chain rules), the subtraction wraps to ~`4,294,967,295 - Δ`, a value far larger than `expected_epoch_timespan * 4` (≈4,838,400 for mainnet). The clamp then forces `actual_timespan = expected_epoch_timespan * 4`, i.e. the *maximum allowed difficulty decrease* (4x easier target), even though the real (negative) timespan should represent blocks mined unusually fast and should instead trigger the maximum difficulty *increase*.

This `verify_header_chain_*` logic is the same code path invoked from the light-client circuit to validate Bitcoin header chains and advance `LatestDaState.current_target_bits`/`total_work` [5](#0-4) , so the wrapped, incorrect target becomes part of the proven DA state rather than a native-only artifact.

### Impact Explanation
A miner who mines the final block of a difficulty epoch (a permissionless, unprivileged action requiring no majority hashrate — anyone who finds one valid block at the right height can do this) can set that block's timestamp earlier than the epoch-start block's timestamp while still satisfying `verify_timestamp`'s median-of-11 rule. This forces the light client's accepted `current_target_bits` for the next epoch to the easiest permitted value (4x looser) instead of the correct (tighter) target that real elapsed time would produce. This breaks the binding between "the proof-of-work target computed by real Bitcoin consensus" and "the target accepted by Citrea's DA verifier/light-client circuit," weakening the PoW threshold that Citrea's total-work/heaviest-chain selection relies on for its light client and downstream trust assumptions (e.g., which DA chain is considered canonical).

### Likelihood Explanation
Exploitability requires finding a single valid PoW block at a specific height (the last block of a 2016-block epoch) with a self-chosen timestamp earlier than the recorded epoch-start timestamp but still greater than the median of the previous 11 blocks — a condition well within a miner's normal timestamp-setting freedom. No elevated role, majority hashrate, or protocol-level privilege is required, only mining ability, making this reachable by an ordinary, unprivileged network participant.

### Recommendation
Use checked/saturating arithmetic (or widen to `i64`) for `actual_timespan = last_timestamp.checked_sub(epoch_start_time)`, and explicitly handle the underflow case by clamping to the minimum allowed timespan (`expected_epoch_timespan / 4`, matching real Bitcoin Core's behavior of clamping a negative timespan to the floor, not the ceiling).

### Proof of Concept
1. At epoch boundary N, the epoch-start block (`epoch_block == 0`) sets `epoch_start_time = T0`.
2. The block 2015 positions later (`epoch_block == BLOCKS_PER_EPOCH - 1`) is mined by the attacker, who sets its embedded timestamp `T_end < T0`, while still keeping `T_end` greater than the median of the previous 11 blocks' timestamps (satisfiable because those 11 blocks can also carry manipulated-but-valid timestamps or simply cluster near `T_end`).
3. `verify_header_chain_common` accepts the block because `verify_timestamp` only checks against the median-of-11, not against `epoch_start_time` [6](#0-5) .
4. `calculate_new_difficulty(T0, T_end, ...)` computes `actual_timespan = T_end - T0` which underflows to a huge `u32`, gets clamped to `expected_epoch_timespan * 4`, and the next epoch's `current_target_bits` is set to the easiest permitted target instead of a tighter one [7](#0-6) .

### Citations

**File:** crates/bitcoin-da/src/verifier.rs (L336-357)
```rust
        let epoch_block = block_header.height() % BLOCKS_PER_EPOCH;

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
                block_header.bits(),
                network_constants.max_target,
                EXPECTED_EPOCH_TIMESPAN,
            );
            target_to_bits(&next_target)
        } else {
            block_header.bits()
        };
```

**File:** crates/bitcoin-da/src/verifier.rs (L568-574)
```rust
        // Check 6: valid timestamp
        if !verify_timestamp(
            block_header.time().secs() as u32,
            latest_da_state.prev_11_timestamps,
        ) {
            return Err(ValidationError::InvalidTimestamp);
        }
```

**File:** crates/bitcoin-da/src/verifier.rs (L580-585)
```rust
/// Verifies the block time against the median of the previous 11 blocks' timestamps
fn verify_timestamp(block_time: u32, mut prev_11_timestamps: [u32; 11]) -> bool {
    prev_11_timestamps.sort_unstable();
    let median_time = prev_11_timestamps[5];
    block_time > median_time
}
```

**File:** crates/bitcoin-da/src/verifier.rs (L677-702)
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
```

**File:** crates/light-client-prover/src/circuit/mod.rs (L1-1)
```rust
//! # Light Client Circuit Module
```
