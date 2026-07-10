### Title
Difficulty Retarget Uses Mainchain Block Timestamp Instead of Fork Ancestor Timestamp, Corrupting PoW Validation for Fork Blocks — (`contract/src/dogecoin.rs`, `contract/src/bitcoin.rs`, `contract/src/litecoin.rs`)

---

### Summary

`get_header_by_height` always resolves blocks from `mainchain_height_to_header`, never from fork-only entries in `headers_pool`. When `get_next_work_required` calls `get_header_by_height(height_first)` to obtain the interval-start timestamp for a difficulty retarget, it silently reads the **mainchain** block at that height instead of the **fork's actual ancestor** at that height. This is the direct analog of the Teller bug: a stale/original value (mainchain block timestamp) is substituted for the correct current value (fork ancestor timestamp), corrupting a critical accounting invariant — here the expected PoW target rather than a token balance.

---

### Finding Description

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header          // ← only mainchain entries
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [1](#0-0) 

Fork blocks are stored exclusively via `store_fork_header`, which inserts only into `headers_pool` and never into `mainchain_height_to_header`:

```rust
fn store_fork_header(&mut self, header: &ExtendedHeader) {
    self.headers_pool.insert(&header.block_hash, header);
}
``` [2](#0-1) 

So `get_header_by_height` is structurally incapable of returning a fork block — it always returns the mainchain block at the requested height.

**Dogecoin (most severe — every block is a retarget after height 145 000):**

After block 145 000, `difficulty_adjustment_interval` is set to `1`, so every block triggers a retarget: [3](#0-2) 

With `difficulty_adjustment_interval = 1`, `blocks_to_go_back` resolves to `1` for any block above height 0, so `height_first = prev_block_header.block_height - 1`: [4](#0-3) 

Suppose a fork diverges at height H (fork block `F_H` vs mainchain block `M_H`). When the relayer submits fork block `F_{H+2}`:
- `prev_block_header` = `F_{H+1}` (correctly fetched from `headers_pool` via `get_prev_header`)
- `height_first` = H
- `get_header_by_height(H)` returns **`M_H`** (mainchain block), not **`F_H`** (fork ancestor)
- `first_block_time` = `M_H.time` ← **wrong value**

The correct value is `F_H.time`. Since `M_H` and `F_H` are different blocks at the same height, their timestamps differ. The developer even left a TODO acknowledging this exact concern: [5](#0-4) 

The corrupted `first_block_time` feeds directly into `calculate_next_work_required`:

```rust
let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;
``` [6](#0-5) 

A wrong `modulated_timespan` produces a wrong `new_target`, which is then compared against `block_header.bits` in `check_pow`. If the computed target is looser than the correct one, a fork block with insufficient real PoW passes validation.

**Bitcoin / Litecoin (triggered at retarget boundaries when fork depth > `difficulty_adjustment_interval`):**

The same structural flaw exists: [7](#0-6) [8](#0-7) 

For Bitcoin/Litecoin the fork must diverge more than 2016 blocks before the retarget boundary for the wrong block to be fetched, making it harder to trigger but not impossible.

---

### Impact Explanation

An attacker who submits a crafted fork can cause the contract to compute an incorrect (lower) difficulty target for their fork blocks at retarget boundaries. A lower target means the PoW hash threshold is relaxed, so the attacker's fork block passes `check_pow` with less actual work than the protocol requires. If the fork's cumulative chain work still exceeds the mainchain tip (which is the reorg trigger), the contract promotes the fork to mainchain, permanently corrupting `mainchain_tip_blockhash`, `mainchain_height_to_header`, and `mainchain_header_to_height`. All downstream consumers of `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` then operate against a fraudulent canonical chain, producing false positive transaction inclusion proofs.

---

### Likelihood Explanation

For **Dogecoin** (post-145 000): the bug fires for every fork block after the first, requiring only a two-block fork — a trivially short submission sequence. Any unprivileged caller of `submit_blocks` can trigger it by supplying two consecutive adversarial Dogecoin headers that diverge from the stored mainchain at any height above 145 000. No privileged role, key leak, or social engineering is needed.

For **Bitcoin/Litecoin**: requires a fork depth exceeding 2016 blocks, which is a significant but non-zero real-world scenario (deep reorg attack or a long-running competing chain).

---

### Recommendation

Replace `get_header_by_height` (mainchain-only lookup) with an ancestor-walk that follows `prev_block_hash` links through `headers_pool` starting from `prev_block_header`. This correctly resolves the interval-start block regardless of whether it is on the mainchain or on the fork being validated:

```rust
// Walk back `blocks_to_go_back` steps through headers_pool
let mut cursor = prev_block_header.clone();
for _ in 0..blocks_to_go_back {
    cursor = blocks_getter.get_prev_header(&cursor.block_header);
}
let first_block_time = cursor.block_header.time;
```

This mirrors how `get_prev_header` already correctly traverses fork chains via `headers_pool` and is consistent with how Zcash's `zcash_get_next_work_required` walks the chain using `get_prev_header` exclusively, never `get_header_by_height`. [9](#0-8) 

---

### Proof of Concept

**Dogecoin scenario (post-145 000):**

1. Mainchain state: tip at height H+10. Blocks `M_H`, `M_{H+1}`, … are in `mainchain_height_to_header`. `M_H.time = T_M`.

2. Attacker calls `submit_blocks([F_H, F_{H+1}, F_{H+2}, …])` where `F_H.prev = M_{H-1}.hash` (fork diverges at H). `F_H.time = T_F` where `T_F > T_M` (attacker sets a later timestamp on the fork block).

3. `F_H` is stored via `store_fork_header` → only in `headers_pool`, not in `mainchain_height_to_header`.

4. `F_{H+1}` is submitted. `check_pow` calls `get_next_work_required`. Since `difficulty_adjustment_interval = 1`, `height_first = H`. `get_header_by_height(H)` returns `M_H` with `time = T_M`.

5. `modulated_timespan = F_{H+1}.time - T_M`. Because `T_M < T_F`, this timespan is **larger** than the correct `F_{H+1}.time - T_F`, producing a **larger** (easier) target.

6. The attacker's `F_{H+1}.bits` encodes this easier target and passes `check_pow`. The actual PoW hash only needs to meet the inflated target, requiring less real work.

7. Repeated for every subsequent fork block. If the fork's cumulative chain work exceeds the mainchain tip, `reorg_chain` is triggered and the fraudulent fork becomes the canonical chain. [10](#0-9) [11](#0-10)

### Citations

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```

**File:** contract/src/lib.rs (L670-682)
```rust
impl BlocksGetter for BtcLightClient {
    fn get_prev_header(&self, current_header: &LightHeader) -> ExtendedHeader {
        self.headers_pool
            .get(&current_header.prev_block_hash)
            .unwrap_or_else(|| env::panic_str("PrevBlockNotFound"))
    }

    fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
        self.mainchain_height_to_header
            .get(&height)
            .and_then(|hash| self.headers_pool.get(&hash))
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
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

**File:** contract/src/dogecoin.rs (L307-309)
```rust
    let modulated_timespan = i64::from(prev_block_header.block_header.time) - first_block_time;

    let mut modulated_timespan = retarget_timespan + (modulated_timespan - retarget_timespan) / 8;
```

**File:** contract/src/bitcoin.rs (L78-87)
```rust
    let first_block_height =
        prev_block_header.block_height - (config.difficulty_adjustment_interval - 1);

    let interval_tail_extend_header = blocks_getter.get_header_by_height(first_block_height);
    calculate_next_work_required(
        config,
        prev_block_header,
        interval_tail_extend_header.block_header.time.into(),
    )
}
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

**File:** contract/src/zcash.rs (L87-103)
```rust
    let mut current_header = prev_block_header.clone();
    let mut total_target = U256::ZERO;
    let mut median_time = [0u32; MEDIAN_TIME_SPAN];

    let prev_block_median_time_past = {
        for i in 0..usize::try_from(config.pow_averaging_window).unwrap() {
            if i < MEDIAN_TIME_SPAN {
                median_time[i] = current_header.block_header.time;
            }

            let (sum, overflow) =
                total_target.overflowing_add(target_from_bits(current_header.block_header.bits));
            require!(!overflow, "Addition of U256 values overflowed");
            total_target = sum;

            current_header = prev_block_getter.get_prev_header(&current_header.block_header);
        }
```
