### Title
Stale Mainchain State Used for Fork Difficulty Calculation — (`contract/src/dogecoin.rs`)

### Summary
In `get_next_work_required` inside `contract/src/dogecoin.rs`, the difficulty calculation for fork blocks fetches the "first block" of the retarget interval from the **mainchain** (`get_header_by_height`) rather than walking back through the fork's actual ancestors. When a fork block is being validated, the mainchain block at `height_first` may be a completely different block than the fork's ancestor at that height. Using the mainchain block's timestamp instead of the fork ancestor's timestamp produces an incorrect expected difficulty, allowing an attacker to submit fork blocks whose `bits` field would otherwise be rejected.

A developer-inserted TODO comment in the code explicitly acknowledges this uncertainty:

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
```

### Finding Description

In `dogecoin.rs`, `get_next_work_required` computes the retarget interval's "first block" time as:

```rust
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
```

`get_header_by_height` is implemented in `lib.rs` as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
```

This always reads from `mainchain_height_to_header`, the **current canonical chain** mapping. When the block being validated is a fork block (its `prev_block_header` was stored via `store_fork_header`, not `store_block_header`), the fork's ancestor at `height_first` may be a different block than the mainchain block at that height. The code uses the mainchain block's timestamp (`T_mainchain`) instead of the fork ancestor's timestamp (`T_fork`).

For Dogecoin after block 145,000, `difficulty_adjustment_interval = 1`, so `blocks_to_go_back = 1` and `height_first = prev_block_header.block_height - 1`. This means **every single fork block submission** after block 145,000 is affected — the difficulty calculation for any fork block at height H+1 uses the mainchain block at height H-1's timestamp, not the fork's block at H-1.

The Digishield formula in `calculate_next_work_required`:

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
```

If `T_mainchain < T_fork` (mainchain block at H-1 is older than the fork block at H-1), `modulated_timespan` is inflated, producing a larger (easier) target. The attacker can set the fork block at H-1's timestamp up to `MAX_FUTURE_BLOCK_TIME_LOCAL` (2 hours) ahead of the current time, while the mainchain block at H-1 has a normal timestamp (~60 seconds old). This creates a systematic timestamp delta that shifts the computed difficulty downward.

The same structural bug exists in `bitcoin.rs` and `litecoin.rs` (both call `blocks_getter.get_header_by_height(first_block_height)` without the TODO comment), but is far less impactful there because the retarget interval is 2016 blocks — a fork would need to diverge more than 2015 blocks ago for the mainchain and fork ancestors to differ at `height_first`.

### Impact Explanation

An attacker can submit Dogecoin fork blocks whose `bits` field encodes a difficulty that is lower than what the real Dogecoin protocol requires. The contract's `check_pow` enforces `expected_bits == block_header.bits`, but `expected_bits` is computed from the wrong (mainchain) timestamp, so the check passes for an invalid difficulty. The fork blocks are stored in `headers_pool`. If the fork accumulates enough chainwork (even at reduced difficulty), `submit_block_header_inner` triggers `reorg_chain`, promoting the fork to the canonical chain. Once promoted, `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` will return `true` for transactions in those invalid fork blocks, corrupting SPV proofs for any downstream NEAR contract consuming this light client.

### Likelihood Explanation

The entry point is `submit_blocks`, which is callable by any trusted relayer (or any account if the `UnrestrictedSubmitBlocks` role is granted). The attacker needs to:
1. Submit a fork block at height H-1 with a timestamp significantly later than the mainchain block at H-1 (up to 2 hours ahead is permitted by `MAX_FUTURE_BLOCK_TIME_LOCAL`).
2. Submit a fork block at height H.
3. Submit a fork block at height H+1 — the difficulty check uses the wrong timestamp and accepts an incorrect `bits` value.

No privileged key or social engineering is required. The manipulation is bounded by Digishield's clamping (`min_timespan`/`max_timespan`), but even a partial difficulty reduction allows submitting blocks that the real Dogecoin network would reject, corrupting the light client's canonical chain.

### Recommendation

Replace the `get_header_by_height` call in all three chain-specific `get_next_work_required` functions with an ancestor walk that follows `prev_block_hash` links through `headers_pool` until reaching `height_first`. This ensures the timestamp used for difficulty calculation is always the fork's actual ancestor, not the mainchain block at the same height. The existing TODO comment in `dogecoin.rs` already identifies this as an open correctness question.

### Proof of Concept

1. Contract is initialized for Dogecoin mainnet at height ≥ 145,001.
2. Mainchain block at height H-1 has timestamp `T0 = now - 60s`.
3. Attacker calls `submit_blocks` with a fork block at height H-1 whose `prev_block_hash` points to the mainchain block at H-2, but whose `time = T0 + 7000s` (within `MAX_FUTURE_BLOCK_TIME_LOCAL = 7200s`). This fork block passes `check_pow` because its own difficulty is checked against the mainchain's expected bits at H-1 (which is computed correctly using the mainchain ancestor at H-2).
4. Attacker submits a fork block at height H with `time = T0 + 7060s`.
5. Attacker submits a fork block at height H+1. `get_next_work_required` computes `height_first = H - 1` and calls `get_header_by_height(H-1)`, returning the **mainchain** block with `time = T0`. The computed `modulated_timespan = (T0 + 7060s) - T0 = 7060s`, clamped to `max_timespan = 90s`, producing a lower difficulty than the fork's actual ancestor (which has `time = T0 + 7000s`, giving `modulated_timespan = 60s`). The expected `bits` is set to an easier value. The attacker's fork block at H+1 passes `check_pow` with this easier difficulty, even though the real Dogecoin network would reject it.
6. If the fork's cumulative chainwork exceeds the mainchain's, `reorg_chain` is triggered, and the invalid fork becomes the canonical chain. SPV proofs against these blocks will return `true`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/dogecoin.rs (L244-248)
```rust
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
```

**File:** contract/src/dogecoin.rs (L286-297)
```rust
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

**File:** contract/src/dogecoin.rs (L300-332)
```rust
// source https://github.com/dogecoin/dogecoin/blob/2c513d0172e8bc86fe9a337693b26f2fdf68a013/src/dogecoin.cpp#L41
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

**File:** contract/src/lib.rs (L562-566)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L677-682)
```rust
    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }
```

**File:** contract/src/bitcoin.rs (L78-86)
```rust
    let first_block_height =
        prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```

**File:** contract/src/litecoin.rs (L86-93)
```rust
    let first_block_height = prev_block_header.block_height - blocks_to_go_back;

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
```
