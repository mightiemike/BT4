### Title
Stale Mainchain Ancestor Timestamp Used in Fork Difficulty Validation — (`contract/src/dogecoin.rs`)

### Summary

In `get_next_work_required()` for Dogecoin, when computing the expected difficulty for a block on a fork chain, the contract fetches the retarget-window's first-block timestamp from the **mainchain height index** rather than walking the fork's actual ancestor chain. This is the direct analog of the external report's `exchangeRateStored` bug: a stored/indexed value is used in place of the correct current value, producing an incorrect difficulty calculation for fork blocks whose ancestor chain diverged before the retarget boundary.

### Finding Description

In `contract/src/dogecoin.rs`, the function `get_next_work_required()` computes the expected `bits` for an incoming block. At a retarget boundary it must find the timestamp of the block at `height_first` (the start of the retarget window) to feed into `calculate_next_work_required()`. [1](#0-0) 

The code does this:

```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
```

`get_header_by_height` resolves against the **mainchain height index** (`mainchain_height_to_header`). When the block being validated is on a fork that branched off before `height_first`, the mainchain block at that height is a **different block** from the fork's true ancestor at that height. The two blocks can carry different `time` values, so `first_block_time` is stale/wrong relative to the fork's actual history. [2](#0-1) 

The developers themselves flagged this with a `TODO` comment, confirming awareness that the mainchain lookup is not guaranteed to be correct for fork validation.

The incorrect `first_block_time` is then passed directly into `calculate_next_work_required()`: [3](#0-2) 

which computes `modulated_timespan` and derives the expected target: [4](#0-3) 

A wrong `first_block_time` shifts `modulated_timespan`, which shifts the computed target, which shifts the expected `bits`. The contract then accepts or rejects the submitted block's `bits` field against this wrong expected value.

### Impact Explanation

An unprivileged proof submitter/relayer can craft a Dogecoin fork whose branch point is before a retarget boundary. At the retarget block on the fork, the contract computes the expected difficulty using the mainchain ancestor's timestamp instead of the fork's true ancestor timestamp. If the attacker controls the timestamps of the fork's pre-retarget blocks (timestamps are only loosely constrained by the `time > prev.time` check), they can engineer a discrepancy between the mainchain ancestor's timestamp and the fork ancestor's timestamp. This causes the contract to accept a fork block whose `bits` encodes a **lower difficulty** than the fork's actual history warrants, allowing the fork to accumulate apparent chain work with less real PoW. A successful exploit corrupts the fork-choice decision: a low-work fork can be promoted to the mainchain, invalidating SPV proofs and `verify_transaction_inclusion` results that downstream contracts rely on.

### Likelihood Explanation

Dogecoin uses per-block Digishield retargeting (every block after height 145,000), so every single Dogecoin block submitted on a fork that diverged before the previous block triggers this code path. The attacker only needs to submit a sequence of valid-PoW fork headers with crafted timestamps; no privileged keys or special roles are required. The entry point is the public `submit_blocks()` method. [5](#0-4) 

### Recommendation

Replace the mainchain height lookup with an ancestor walk along the fork's own chain. Starting from `prev_block_header`, walk backwards `blocks_to_go_back` steps through `headers_pool` (following `prev_block_hash` links) to find the true ancestor at `height_first`. This mirrors how Bitcoin Core's `GetAncestor` works and is the correct approach for validating blocks that may be on a fork. The existing `headers_pool` already stores all fork headers, so the data is available; only the lookup strategy needs to change.

### Proof of Concept

1. Deploy the Dogecoin variant of the contract.
2. Submit a valid mainchain sequence up to height H (past block 145,000 so Digishield is active). The mainchain block at height H−1 has timestamp `T_main`.
3. Submit a fork starting at height H−K (K > 1) with crafted timestamps. Ensure the fork's block at height H−1 has timestamp `T_fork ≠ T_main`.
4. Submit the fork's retarget block at height H. The contract calls `get_header_by_height(H−1)` and retrieves the **mainchain** block with timestamp `T_main`, not the fork's block with `T_fork`.
5. `calculate_next_work_required` computes expected `bits` using `T_main`, yielding `bits_wrong`.
6. Supply a fork block whose `bits` field matches `bits_wrong` (lower difficulty than the fork's true retarget would require). The contract accepts it.
7. Continue extending the fork with this artificially easy difficulty. Once the fork's `chain_work` exceeds the mainchain's, `reorg_chain` is triggered, promoting the low-work fork to the mainchain and corrupting all subsequent `verify_transaction_inclusion` results. [6](#0-5)

### Citations

**File:** contract/src/dogecoin.rs (L166-204)
```rust
    pub(crate) fn submit_block_header(
        &mut self,
        header: (Header, Option<AuxData>),
        skip_pow_verification: bool,
    ) {
        let (block_header, aux_data) = header;

        let prev_block_header = self.get_prev_header(&block_header);
        let current_block_hash = block_header.block_hash();

        if !skip_pow_verification {
            self.check_target(&block_header, &prev_block_header);

            if let Some(ref aux_data) = aux_data {
                self.check_aux(&block_header, aux_data);
            } else {
                let pow_hash = block_header.block_hash_pow();
                // Check if the block hash is less than or equal to the target
                require!(
                    U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
                    format!("block should have correct pow")
                );
            }
        }

        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(block_header.bits));
        require!(!overflow, "Addition of U256 values overflowed");

        let current_header = ExtendedHeader {
            block_header: block_header.clone().into_light(),
            block_hash: current_block_hash,
            chain_work: current_block_computed_chain_work,
            block_height: 1 + prev_block_header.block_height,
        };

        self.submit_block_header_inner(current_header, &prev_block_header);
    }
```

**File:** contract/src/dogecoin.rs (L229-297)
```rust
fn get_next_work_required(
    config: &DogecoinConfig,
    block_header: &Header,
    prev_block_header: &ExtendedHeader,
    blocks_getter: &impl BlocksGetter,
) -> u32 {
    // Dogecoin: Special rules for minimum difficulty blocks with Digishield
    if allow_min_difficulty_for_block(config, block_header, prev_block_header) {
        // Special difficulty rule for testnet:
        // If the new block's timestamp is more than 2* nTargetSpacing minutes
        // then allow mining of a min-difficulty block.
        return config.proof_of_work_limit_bits;
    }

    // Only change once per difficulty adjustment interval
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };

    if (prev_block_header.block_height + 1) % difficulty_adjustment_interval != 0 {
        if config.pow_allow_min_difficulty_blocks {
            // Special difficulty rule for testnet:
            // If the new block's timestamp is more than 2* 10 minutes
            // then allow mining of a min-difficulty block.
            if block_header.time
                > prev_block_header.block_header.time + config.pow_target_spacing * 2
            {
                return config.proof_of_work_limit_bits;
            }

            // Return the last non-special-min-difficulty-rules-block
            let mut current_block_header = prev_block_header.clone();

            while current_block_header.block_header.bits == config.proof_of_work_limit_bits
                && current_block_header.block_height % config.difficulty_adjustment_interval != 0
            {
                current_block_header =
                    blocks_getter.get_prev_header(&current_block_header.block_header);
            }

            return current_block_header.block_header.bits;
        }

        return prev_block_header.block_header.bits;
    }

    // Litecoin: This fixes an issue where a 51% attack can change difficulty at will.
    // Go back the full period unless it's the first retarget after genesis. Code courtesy of Art Forz
    let mut blocks_to_go_back = difficulty_adjustment_interval - 1;
    if prev_block_header.block_height + 1 != difficulty_adjustment_interval {
        blocks_to_go_back = difficulty_adjustment_interval;
    }

    // Go back by what we want to be 14 days worth of blocks
    let height_first = prev_block_header
        .block_height
        .checked_sub(blocks_to_go_back)
        .unwrap_or_else(|| env::panic_str("Height underflow when calculating first block height"));

    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

**File:** contract/src/dogecoin.rs (L307-332)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;

    let min_timespan = retarget_timespan - (retarget_timespan / 4);
    let max_timespan = retarget_timespan + (retarget_timespan / 2);

    if modulated_timespan < min_timespan {
        modulated_timespan = min_timespan;
    } else if modulated_timespan > max_timespan {
        modulated_timespan = max_timespan;
    }

    let new_target = target_from_bits(prev_block_header.block_header.bits);

    let (mut new_target, new_target_overflow) =
        new_target.overflowing_mul(<i64 as TryInto<u64>>::try_into(modulated_timespan).unwrap());
    require!(!new_target_overflow, "new target overflow");
    new_target =
        new_target / U256::from(<i64 as TryInto<u64>>::try_into(retarget_timespan).unwrap());

    if new_target > config.pow_limit {
        new_target = config.pow_limit;
    }

    new_target.target_to_bits()
```
