### Title
Unsigned-subtraction underflow in Bitcoin difficulty-adjustment causes Citrea's DA header-chain verification to diverge from real Bitcoin consensus - (File: crates/bitcoin-da/src/verifier.rs)

### Summary
`calculate_new_difficulty` in `crates/bitcoin-da/src/verifier.rs` computes the actual epoch timespan as an unsigned `u32` subtraction, `last_timestamp - epoch_start_time`. Real Bitcoin Core computes this quantity as a **signed** `int64_t`, because a block's timestamp is not required to be monotonically increasing across an entire 2016-block epoch — it is only required to exceed the median-time-past of the previous 11 blocks. This means it is fully possible, under real Bitcoin consensus rules, for the timestamp of the last block of a difficulty period to be *less than* the timestamp of the first block of that period, giving Bitcoin Core a negative `nActualTimespan`. Citrea's implementation cannot represent this as negative; the unsigned subtraction wraps, producing a value near `u32::MAX`, which then gets clamped to the *maximum* allowed timespan instead of the *minimum* allowed timespan that real Bitcoin Core would clamp to. This flips the resulting difficulty adjustment (an "easier" retarget instead of a "harder" one), so the `current_target_bits` Citrea computes as canonical for the next epoch can diverge from the value actually enforced by the Bitcoin network for the same historical epoch.

### Finding Description
The relevant code: [1](#0-0) 

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

`epoch_start_time` and `last_timestamp` (the timestamp of the last block of the epoch, at `epoch_block == BLOCKS_PER_EPOCH - 1`) are both `u32`, and Bitcoin's consensus rules only guarantee `block_header.time() > median(prev_11_timestamps)` (enforced separately by `verify_timestamp`), not that timestamps increase monotonically relative to the start of the epoch. Bitcoin Core's own difficulty-adjustment algorithm (`CalculateNextWorkRequired`) uses signed arithmetic (`int64_t nActualTimespan = pindexLast->GetBlockTime() - nFirstBlockTime;`) precisely to correctly handle a negative timespan by clamping it downward (to `nPowTargetTimespan/4`, i.e., a maximal difficulty increase).

In Citrea's code, if `last_timestamp < epoch_start_time` (a scenario allowed by consensus and that has historically occurred on the Bitcoin network), the `u32` subtraction underflows. Depending on the build's overflow-check configuration this either panics (a denial-of-service style failure, out of scope per the rules) or — in a release/guest build without `overflow-checks` enabled (no `overflow-checks = true` setting was found anywhere in the repository) — wraps to a value close to `u32::MAX`. That wrapped huge value fails the `< expected_epoch_timespan / 4` check and instead satisfies `> expected_epoch_timespan * 4`, clamping `actual_timespan` to the **maximum** allowed value. This is the exact opposite of the correct signed-arithmetic clamp to the **minimum** allowed value that real Bitcoin consensus performs in this situation.

This computed `actual_timespan` directly feeds into `current_target_bits` via `target_to_bits(&next_target)`, and that value is what `verify_header_chain_common` expects for every subsequent block's `bits()` field in `verify_header_chain_mainnet`/`testnet4`/`signet` (used to validate the DA header chain and, transitively, the light client proof's `LatestDaState.current_target_bits`, which is committed to in the light-client circuit output): [2](#0-1) 

Because Citrea's computed target diverges from the target Bitcoin's real network actually enforces for that epoch, the DA header-chain verifier binding (`bits field computed by Citrea == bits field Bitcoin consensus actually produced`) is broken in this edge case.

### Impact Explanation
This breaks the equality that must hold between "the difficulty/target that real Bitcoin consensus produced for a given epoch" and "the difficulty/target Citrea's `BitcoinVerifier` computes and requires for that same epoch." Two consequences follow, both falling within accepted Critical impacts:
- If the real, canonical Bitcoin chain contains an epoch boundary with this exact timestamp condition (negative real timespan under signed math), Citrea's light client/DA verifier will compute the wrong expected `bits` for the next epoch and reject the legitimate header chain (`ValidationError::InvalidBlockBits`), permanently halting DA-chain progression for the light client prover and the batch prover pipeline that depends on it — i.e., a true state transition (the honest, canonical Bitcoin chain) becomes unprovable / un-followable, which can permanently freeze bridge and rollup progress.
- Conversely, since the wrong (looser) target is now the one Citrea's verifier requires, an attacker able to mine (or who already possesses) a lower-difficulty alternate header segment matching Citrea's incorrectly-computed, easier target for that epoch could have that segment accepted by Citrea's header-chain verification even though it would not satisfy the difficulty rule real Bitcoin enforces for the equivalent epoch, undermining the DA-inclusion/completeness guarantee the light client relies on.

### Likelihood Explanation
This is not a contrived or purely theoretical construction: negative real epoch timespans (last block earlier than the epoch's first block, under the median-time-past rule) have precedent on Bitcoin's actual history, since only the median-of-11 constraint is required, not monotonicity across the whole 2016-block epoch. The bug is deterministic and reachable purely by observing/replaying real Bitcoin DA data; no privileged role, prover, or sequencer collaboration is required — an ordinary user or observer feeding legitimate Bitcoin headers through the DA verifier at any epoch boundary exhibiting this timing pattern will trigger the divergence.

### Recommendation
Compute `actual_timespan` using signed 64-bit (or wider) arithmetic exactly as Bitcoin Core does, e.g. `let actual_timespan: i64 = last_timestamp as i64 - epoch_start_time as i64;`, clamp using `i64` comparisons against `expected_epoch_timespan as i64 / 4` and `* 4`, and only cast back to an unsigned type after clamping (the clamped result is always non-negative). Apply this fix uniformly across `verify_header_chain_mainnet`, `verify_header_chain_testnet4`, and `verify_header_chain_signet`, all of which share `calculate_new_difficulty`.

### Proof of Concept
Deterministic arithmetic demonstration (no network access required):
1. Suppose `epoch_start_time = 1_700_000_100` and `last_timestamp = 1_700_000_000` (a `-100` second real timespan, legal because it only needs to exceed the median of the previous 11 blocks, not the epoch start time).
2. Citrea: `actual_timespan = last_timestamp.wrapping_sub(epoch_start_time)` (or equivalent unsigned subtraction) `= u32::MAX - 99 = 4294967196` (wraps because `last_timestamp < epoch_start_time`).
3. `expected_epoch_timespan * 4` (e.g., for mainnet, `14 days * 4` in seconds `= 4_838_400`) is far smaller than `4294967196`, so the `else if actual_timespan > expected_epoch_timespan * 4` branch fires, clamping to the **maximum** timespan (`expected_epoch_timespan * 4`), which lowers difficulty.
4. Real Bitcoin Core: `nActualTimespan = -100`, which is `< nPowTargetTimespan/4`, so it clamps to the **minimum** timespan (`expected_epoch_timespan / 4`), which raises difficulty.
5. The two implementations produce opposite-direction retargets from the same real block timestamps, so `target_to_bits` yields different `current_target_bits` values, breaking the equality `Citrea-expected bits == Bitcoin-consensus bits` for the next epoch's blocks.

### Citations

**File:** crates/bitcoin-da/src/verifier.rs (L345-371)
```rust
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

        let total_work = U256::from_be_bytes(latest_da_state.total_work)
            .saturating_add(&work_add)
            .to_be_bytes();

        Ok(LatestDaState {
            block_hash: block_header.hash().to_byte_array(),
            block_height: block_header.height(),
            total_work,
            current_target_bits,
            epoch_start_time,
            prev_11_timestamps,
        })
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
