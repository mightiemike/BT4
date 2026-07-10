After reading all production source files, I need to identify whether the cross-module desynchronization vulnerability class has a concrete analog in this repository.

The reported vulnerability class is: a state variable accumulated across a boundary is read by a consumer module that applies incorrect arithmetic to isolate the relevant portion, causing the variable to be incorrectly reduced and leading to corrupted state.

The closest structural analog in this repository is in the Dogecoin difficulty calculation, where the difficulty module reads from mainchain state to validate fork blocks, but the mainchain state does not reflect the fork's actual ancestor at the queried height.

---

### Title
Cross-Module Desynchronization in Dogecoin Difficulty Calculation Uses Mainchain Ancestor Instead of Fork Ancestor — (`contract/src/dogecoin.rs`)

### Summary

When validating Dogecoin fork blocks, `get_next_work_required` fetches the "first block" of the difficulty adjustment interval via `get_header_by_height`, which reads from `mainchain_height_to_header`. For any fork block at height H+2 or later (where H is the fork point), the block at `height_first` is inside the fork, but `get_header_by_height` returns the **mainchain** block at that height instead of the fork's actual ancestor. This causes the difficulty calculation to use the wrong timestamp, allowing an attacker to submit fork blocks that pass the difficulty check with less PoW than the protocol requires.

### Finding Description

In `get_next_work_required` (`dogecoin.rs`), after block 145,000 the DigiShield protocol sets `difficulty_adjustment_interval = 1`, so `blocks_to_go_back = 1` and `height_first = prev_block_header.block_height - 1`. [1](#0-0) [2](#0-1) 

The code then fetches the first-block timestamp via:

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [3](#0-2) 

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [4](#0-3) 

This reads from `mainchain_height_to_header`, which maps heights to **mainchain** block hashes only. Fork blocks are stored in `headers_pool` but are never inserted into `mainchain_height_to_header`. [5](#0-4) 

**Desynchronization scenario** (DigiShield, interval=1, fork point = H):

| Block being validated | `height_first` | Block returned by `get_header_by_height` | Correct ancestor |
|---|---|---|---|
| F_{H+1} | H−1 | M_{H−1} (shared ancestor) | M_{H−1} ✓ |
| F_{H+2} | H | **M_H** (mainchain block) | **F_H** (fork block) ✗ |
| F_{H+3} | H+1 | **M_{H+1}** (mainchain block) | **F_{H+1}** (fork block) ✗ |

Starting at F_{H+2}, the difficulty calculation uses `M_H.time` instead of `F_H.time`. The attacker controls `F_H.time` (within MTP and future-timestamp bounds), so they can set it significantly later than `M_H.time`, making the correct `actual_timespan` small (high difficulty) while the used `actual_timespan` is large (low difficulty).

The `check_target` call that invokes this path is made unconditionally for all submitted blocks, including fork blocks, before `submit_block_header_inner` decides whether to store them as mainchain or fork: [6](#0-5) 

### Impact Explanation

The DigiShield difficulty formula is:

```rust
let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
``` [7](#0-6) 

With `retarget_timespan = 60 s` and `max_timespan = 90 s`, an attacker who sets `F_H.time` to be 7200 s (2 hours) later than `M_H.time` inflates `actual_timespan` by 7200 s, pushing `modulated_timespan` to the clamped maximum of 90 s instead of the correct 60 s. This reduces the required difficulty by up to **33%** for every fork block from F_{H+2} onward. If the fork accumulates enough chainwork (aided by the reduced per-block difficulty), `reorg_chain` promotes it as the canonical chain, corrupting `mainchain_tip_blockhash`, `mainchain_height_to_header`, and `mainchain_header_to_height`. [8](#0-7) 

### Likelihood

### Citations

**File:** contract/src/dogecoin.rs (L176-188)
```rust
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
```

**File:** contract/src/dogecoin.rs (L244-249)
```rust
    let new_difficulty_protocol = prev_block_header.block_height >= 145_000;
    let difficulty_adjustment_interval = if new_difficulty_protocol {
        1
    } else {
        config.difficulty_adjustment_interval
    };
```

**File:** contract/src/dogecoin.rs (L280-297)
```rust
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

**File:** contract/src/dogecoin.rs (L307-318)
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
```

**File:** contract/src/lib.rs (L563-567)
```rust
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
```

**File:** contract/src/lib.rs (L650-656)
```rust
    fn store_block_header(&mut self, header: &ExtendedHeader) {
        self.mainchain_height_to_header
            .insert(&header.block_height, &header.block_hash);
        self.mainchain_header_to_height
            .insert(&header.block_hash, &header.block_height);
        self.headers_pool.insert(&header.block_hash, header);
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
