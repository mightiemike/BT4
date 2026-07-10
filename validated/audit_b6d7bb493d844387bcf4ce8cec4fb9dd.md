### Title
Dogecoin Fork Difficulty Miscalculation via Mainchain Timestamp Substitution — (File: `contract/src/dogecoin.rs`)

### Summary
The `get_next_work_required` function in `contract/src/dogecoin.rs` fetches the first-block timestamp for the difficulty adjustment window using `blocks_getter.get_header_by_height(height_first)`, which always resolves against the **mainchain** height-to-hash map. When the block being validated belongs to a fork, the mainchain block at that height may differ from the fork's actual ancestor, producing a wrong `first_block_time` and therefore a wrong difficulty target. An unprivileged relayer can trigger this path by submitting a valid fork chain.

### Finding Description

In `get_next_work_required` (Dogecoin build), after the new-difficulty-protocol branch is taken (`prev_block_header.block_height >= 145_000`, so `difficulty_adjustment_interval = 1`), the code computes:

```rust
let mut blocks_to_go_back = difficulty_adjustment_interval - 1; // = 0
if prev_block_header.block_height + 1 != difficulty_adjustment_interval {
    blocks_to_go_back = difficulty_adjustment_interval; // = 1 for any height > 0
}

let height_first = prev_block_header
    .block_height
    .checked_sub(blocks_to_go_back)   // = prev_height - 1
    ...

let first_block_time = blocks_getter
    .get_header_by_height(height_first)   // ← always mainchain lookup
    .block_header
    .time;
```

`get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        ...
}
```

It reads `mainchain_height_to_header`, which maps only mainchain blocks. When the block under validation is on a fork that diverged at height H, the fork's ancestor at height H−1 may carry a different timestamp than the mainchain block at H−1. The code substitutes the mainchain timestamp, producing an incorrect `modulated_timespan` and therefore an incorrect next-target.

The developers themselves flagged this with a TODO comment directly above the call:

```rust
// TODO: check if it is correct to get block header by height from mainchain
// without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
```

The analog to the reported bug is exact: the reported code used `totalSupply(tokenId)` (a global aggregate) where it should have used `balanceOf(sender, tokenId)` (the sender-specific value). Here, the code uses `mainchain_block_at(H−1).time` (a global/mainchain value) where it should use `fork_ancestor_at(H−1).time` (the fork-specific value).

### Impact Explanation

Dogecoin's Digishield formula is:

```
modulated_timespan = retarget_timespan + (actual_timespan − retarget_timespan) / 8
```

clamped to `[min_timespan, max_timespan]` = `[45 s, 90 s]` (with `pow_target_timespan = 60 s`).

An attacker who submits a fork block at height H with a timestamp up to `MAX_FUTURE_BLOCK_TIME_LOCAL = 7200 s` ahead of the mainchain block at H causes `actual_timespan` (as seen by the contract) to be inflated by up to 7200 s. After Digishield damping and clamping, the computed target can reach `max_timespan / retarget_timespan = 90/60 = 1.5×` the previous target — a 33 % difficulty reduction. Fork blocks submitted with this artificially low difficulty are accepted by the contract, accumulate chainwork, and can trigger a chain reorg if the fork grows long enough.

### Likelihood Explanation

The trigger is reachable by any unprivileged relayer via the public `submit_blocks` entry point. Dogecoin's per-block retarget (`difficulty_adjustment_interval = 1` post-145 000) means the miscalculation activates for every fork block after the second one. No privileged role, leaked key, or social engineering is required. The attacker only needs to mine a short fork with a manipulated timestamp on the diverging block.

### Recommendation

Replace the mainchain height lookup with an ancestor walk that follows `prev_block_hash` links, mirroring how `get_prev_header` is used elsewhere:

```rust
// Instead of:
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;

// Walk back blocks_to_go_back steps from prev_block_header:
let mut cursor = prev_block_header.clone();
for _ in 0..blocks_to_go_back {
    cursor = blocks_getter.get_prev_header(&cursor.block_header);
}
let first_block_time = cursor.block_header.time;
```

The same fix should be applied to the analogous `get_header_by_height` calls in `contract/src/bitcoin.rs` and `contract/src/litecoin.rs`.

### Proof of Concept

1. Contract is initialized with a Dogecoin mainchain at height H (≥ 145 000). Mainchain block at H−1 has timestamp `T_main`.
2. Attacker submits a fork block at height H whose `prev_block_hash` points to the same parent as the mainchain block at H. This fork block carries