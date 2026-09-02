## Root cause: unsigned‑subtraction underflow in Bitcoin difficulty retarget diverges from Bitcoin consensus

### Title
Unsafe unsigned subtraction in `calculate_new_difficulty` can underflow and flip the difficulty‑adjustment direction, causing the light client to compute a next‑epoch target that diverges from real Bitcoin consensus — (`File: crates/bitcoin-da/src/verifier.rs`)

### Summary
`calculate_new_difficulty` computes the length of a Bitcoin difficulty epoch as a plain `u32` subtraction of two block timestamps instead of using signed arithmetic (as Bitcoin Core does with `int64_t`). If the timestamp of the last block of an epoch is smaller than the timestamp of the epoch's first block — which Bitcoin consensus permits, since blocks are only required to have a timestamp greater than the median of the previous 11 blocks, not to be monotonically increasing across the whole 2016‑block epoch — the subtraction underflows to a value near `u32::MAX` instead of being handled as a negative timespan.

### Finding Description [1](#0-0) 

```rust
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
    ...
```

`last_timestamp` is the timestamp of the last block (block 2015) of the current retarget epoch, and `epoch_start_time` is the timestamp of the first block (block 0) of that epoch, both threaded through the mainnet/testnet4/signet header-chain verification paths: [2](#0-1) 

Real Bitcoin Core computes this timespan with signed 64‑bit arithmetic (`nActualTimespan = pindexLast->GetBlockTime() - nFirstBlockTime`), which can legitimately be negative and is then clamped into `[expected/4, expected*4]`. This Rust implementation instead performs an **unsigned** subtraction (`u32 - u32`). When `last_timestamp < epoch_start_time`:
- In a debug build, this panics (out of scope, DoS).
- In a release build — the profile normally used to compile the zkVM guest / prover / verifier binaries, where Rust's default `overflow-checks = false` applies — the subtraction silently **wraps around** to a value close to `u32::MAX`.

This is the exact same bug class described in the external report: an unsigned‑integer computation that should model a signed quantity instead wraps to a large bogus value rather than being clamped/handled correctly.

Critically, the wrapped value is not merely "large" — it lands on the **wrong side** of the existing clamp. A genuinely negative timespan (epoch ran backward, meaning blocks arrived unusually fast) should be clamped to the **lower bound** `expected_epoch_timespan/4`, which correctly makes the next epoch's target *smaller* (harder difficulty). Because of the underflow, the value instead lands above `expected_epoch_timespan*4` and gets clamped to the **upper bound**, which makes the next epoch's target *larger* (**easier** difficulty, up to 4x). The retarget algorithm's sign is inverted for this edge case.

### Impact Explanation
This breaks the binding that the DA verifier's `current_target_bits` for a given epoch boundary must equal what real Bitcoin consensus (as enforced by full nodes and miners) would compute. When the underflow condition is hit at an epoch boundary, the light client accepts a next‑epoch PoW target that is up to 4x easier than the real Bitcoin network's retargeted difficulty, in the opposite direction from correct behavior. Because `verify_header_chain` is exactly the function used to validate the Bitcoin block headers backing the light-client/DA proofs (`crates/light-client-prover/src/circuit/mod.rs:707-715`), an incorrect, too-easy target here means the guest can accept a chain of headers that would not satisfy genuine Bitcoin consensus rules for that epoch — a forged/incorrect DA-chain acceptance, i.e., "root/target computed natively (by real Bitcoin) diverges from the root/target computed in the guest."

### Likelihood Explanation
Reachability is constrained: it requires either (a) a genuine, currently-existing or future Bitcoin mainnet epoch boundary where the last block's timestamp happens to be lower than the first block's timestamp of that same epoch — possible under Bitcoin's median‑time‑past rule without violating consensus, exploitable historically via the well‑known "time‑warp" timestamp‑manipulation technique by a miner with sufficient hashpower — or (b) an attacker with modest hashpower manipulating timestamps on a lower-difficulty network (signet/testnet) that uses the same `calculate_new_difficulty` function. I was not able to verify from the index whether any real historical Bitcoin epoch boundary actually triggers this condition, nor could I confirm the release-profile `overflow-checks` setting for the guest/prover build in this repository snapshot (the root `Cargo.toml` `[profile]` section exists but its contents were not retrievable within my tool budget), so I cannot confirm with certainty whether the wraparound panics (DoS, out of scope) or silently succeeds in the actual production build.

### Recommendation
Perform the timespan calculation using signed arithmetic (e.g., `i64`) exactly as Bitcoin Core does, clamp the signed value into `[expected/4, expected*4]`, and only then convert to the unsigned type used for the target computation:
```rust
let mut actual_timespan: i64 = last_timestamp as i64 - epoch_start_time as i64;
if actual_timespan < (expected_epoch_timespan / 4) as i64 {
    actual_timespan = (expected_epoch_timespan / 4) as i64;
} else if actual_timespan > (expected_epoch_timespan * 4) as i64 {
    actual_timespan = (expected_epoch_timespan * 4) as i64;
}
let actual_timespan = actual_timespan as u32; // now safe, always within bounds
```

### Proof of Concept
Concretely, calling `calculate_new_difficulty(epoch_start_time, last_timestamp, ..)` with `last_timestamp < epoch_start_time` (e.g. `epoch_start_time = 1_700_000_100`, `last_timestamp = 1_700_000_000`) causes `last_timestamp - epoch_start_time` to wrap to `u32::MAX - 99`, which is then clamped to `expected_epoch_timespan * 4` (the "epoch took too long" branch) instead of the correct `expected_epoch_timespan / 4` ("epoch took negative/too little time") branch — inverting the intended difficulty correction. I was unable to confirm from the available index whether such a timestamp ordering has occurred, or can be engineered, at a real 2016-block epoch boundary on any network this verifier supports (mainnet/testnet4/signet); this would need to be validated with actual/simulated header data and the build's overflow-check configuration before treating this as a fully proven, exploitable divergence rather than a code-level defect.

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
