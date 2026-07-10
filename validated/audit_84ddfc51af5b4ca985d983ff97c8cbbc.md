### Title
Fork Block Difficulty Calculation Uses Mainchain Block Timestamps Instead of Fork Ancestor Timestamps — (`contract/src/dogecoin.rs`)

### Summary

In the Dogecoin (and Bitcoin/Litecoin) implementations, `get_next_work_required` fetches the "first block" of the difficulty interval via `blocks_getter.get_header_by_height(height_first)`, which always reads from `mainchain_height_to_header`. When the function is called for a **fork block**, the mainchain block at `height_first` may have a completely different timestamp than the fork's actual ancestor at that height. The difficulty calculation is therefore incorrectly influenced by the mainchain's state rather than the fork chain's own history — a direct structural analog to the OracleMaker bug where `maxPositionNotional` (derived from free collateral) incorrectly influenced the position rate.

The developers themselves flagged this with a TODO comment directly above the offending call.

### Finding Description

In `contract/src/dogecoin.rs`, `get_next_work_required` computes the required difficulty for the next block:

```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;

calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

`blocks_getter.get_header_by_height` is implemented in `contract/src/lib.rs` as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
```

It unconditionally reads from `mainchain_height_to_header` — the canonical chain index — regardless of whether the block being validated belongs to a fork.

For Dogecoin after block 145,000, `difficulty_adjustment_interval = 1`, so `blocks_to_go_back = 1` and `height_first = prev_block_header.block_height - 1`. When a fork diverges at height `H-1` or earlier, the fork's ancestor at `H-1` differs from the mainchain block at `H-1`. The Digishield retarget then computes:

```
modulated_timespan = fork_prev_block.time − mainchain_block_at(H-1).time
```

instead of the correct:

```
modulated_timespan = fork_prev_block.time − fork_ancestor_at(H-1).time
```

The same structural flaw exists in `contract/src/bitcoin.rs` and `contract/src/litecoin.rs` for their respective `get_next_work_required` functions, though for those chains the 2016-block interval makes the divergence window much larger.

### Impact Explanation

An attacker who can submit fork blocks controls the timestamp of each fork block (within the MTP lower bound and the `current_time + 2h` upper bound). Because `first_block_time` is taken from the **mainchain** block at `height_first` rather than the fork's own ancestor, the attacker can choose a fork block timestamp that maximises or minimises `modulated_timespan`:

- Setting `fork_prev.time ≫ mainchain_at(H-1).time` pushes `modulated_timespan` toward `max_timespan` (90 s for Dogecoin), raising the target by up to 50% — making subsequent fork blocks easier to mine.
- Setting `fork_prev.time ≪ mainchain_at(H-1).time` (but still above MTP) pushes `modulated_timespan` toward `min_timespan` (45 s), lowering the target by up to 25% — increasing chainwork per fork block.

The contract enforces `expected_bits == block_header.bits`, so it will accept fork blocks whose `bits` match the incorrectly computed `expected_bits`. This means the contract accepts fork blocks that do not satisfy the actual Dogecoin protocol's difficulty requirement, corrupting its view of the fork chain's accumulated work and potentially enabling a chain-reorganization attack with a fork chain that would be invalid on the real Dogecoin network.

### Likelihood Explanation

The Dogecoin deployment is affected for every block above height 145,000 (the entire modern Dogecoin chain). Any fork submission that is at least two blocks deep triggers the incorrect code path. The `submit_blocks` entry point is the production relayer path; any caller who can submit blocks (trusted relayer or a role-bypassing account) can craft fork blocks with adversarial timestamps to exploit this.

### Recommendation

Replace `blocks_getter.get_header_by_height(height_first)` with a function that walks the fork's ancestor chain (via `get_prev_header`) from `prev_block_header` back `blocks_to_go_back` steps, rather than looking up the mainchain index. This ensures the difficulty calculation is based solely on the fork's own history, independent of the mainchain state — mirroring the fix recommended in the OracleMaker report (exclude the extraneous factor from the rate calculation).

### Proof of Concept

1. Contract is initialized with Dogecoin mainnet at height > 145,000. Mainchain block at height `H-1` has timestamp `T_main`.
2. Attacker submits a fork block at height `H-1` with timestamp `T_fork = T_main + 7200` (2 hours ahead, within the allowed window). This fork block passes `check_pow` because its difficulty is computed from the mainchain block at `H-2` (which is shared with the fork at that depth).
3. Attacker submits a fork block at height `H`. The contract calls `get_next_work_required` with `prev_block_header` = fork block at `H-1` and `height_first = H-2`. If the fork diverges at `H-1`, `get_header_by_height(H-2)` returns the mainchain block at `H-2` (same as fork ancestor — no bug yet).
4. Attacker submits a fork block at height `H+1`. Now `height_first = H-1`. `get_header_by_height(H-1)` returns the **mainchain** block at `H-1` with timestamp `T_main`, while the fork's ancestor at `H-1` has timestamp `T_fork = T_main + 7200`. The contract computes `modulated_timespan = fork_block_H.time − T_main` instead of `fork_block_H.time − T_fork`, producing an incorrect `expected_bits` that does not match the actual Dogecoin protocol requirement.
5. The contract accepts the fork block at `H+1` with the wrong difficulty, storing an incorrect `chain_work` value for that block. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contract/src/dogecoin.rs (L244-249)
```rust
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };
```

**File:** contract/src/dogecoin.rs (L278-298)
```rust
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
}
```

**File:** contract/src/dogecoin.rs (L301-332)
```rust
fn calculate_next_work_required(
    config: &DogecoinConfig,
    prev_block_header: &ExtendedHeader,
    first_block_time: i64,
) -> u32 {
    let retarget_timespan = config.pow_target_timespan;
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

**File:** contract/src/lib.rs (L677-683)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
}
```
