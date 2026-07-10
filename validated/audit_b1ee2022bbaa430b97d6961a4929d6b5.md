### Title
Difficulty Retarget Uses Mainchain Height Lookup Instead of Fork Ancestor, Enabling Manipulated Difficulty for Fork Blocks - (File: contract/src/dogecoin.rs, contract/src/bitcoin.rs, contract/src/litecoin.rs)

### Summary
When validating fork blocks at difficulty retarget boundaries, `get_next_work_required` calls `get_header_by_height` to retrieve the first block of the retarget interval. This function exclusively queries `mainchain_height_to_header`, which only contains mainchain blocks. Fork blocks are stored in `headers_pool` but never in `mainchain_height_to_header`. As a result, when a fork diverges before the start of a retarget interval, the difficulty calculation uses the mainchain's block at that height — not the fork's ancestor — producing a stale, incorrect timespan and a manipulable difficulty target. The developer explicitly flagged this in a TODO comment in `dogecoin.rs`.

### Finding Description

In `submit_block_header_inner`, fork blocks are stored via `store_fork_header`, which only inserts into `headers_pool`: [1](#0-0) 

The `get_header_by_height` implementation only queries `mainchain_height_to_header`: [2](#0-1) 

All three chain-specific `get_next_work_required` functions call `get_header_by_height` to retrieve the first block of the retarget interval:

**Bitcoin** (`bitcoin.rs`): [3](#0-2) 

**Litecoin** (`litecoin.rs`): [4](#0-3) 

**Dogecoin** (`dogecoin.rs`) — with an explicit developer TODO acknowledging the problem: [5](#0-4) 

When a fork diverges at height `H` and the retarget interval's first block is at height `F ≤ H`, `get_header_by_height(F)` returns the **mainchain's** block at height `F` (timestamp `T_mainchain_F`), not the fork's block at that height (timestamp `T_fork_F`). The attacker controls `T_fork_F` (subject only to the MTP check, which traverses the fork chain correctly via `get_prev_header`), so they can set `T_fork_F > T_mainchain_F`. The difficulty calculation then computes:

```
actual_time_taken = T_prev_fork - T_mainchain_F
                  > T_prev_fork - T_fork_F   (correct value)
```

A larger `actual_time_taken` produces a higher (easier) target, allowing the fork's retarget block to be submitted with less PoW than the protocol requires.

### Impact Explanation

An attacker who submits a fork diverging before a retarget interval boundary can craft fork block timestamps such that the stale mainchain timestamp at `first_block_height` inflates `actual_time_taken`, yielding an easier difficulty target. For **Dogecoin** (post-height 145,000, `difficulty_adjustment_interval = 1`), the retarget fires every block, so the stale lookup affects every fork block two or more heights after the divergence point — the attacker gets a systematically easier target for the entire fork. For **Bitcoin** and **Litecoin** (interval = 2016 blocks), the easier target applies to the entire next 2016-block interval. In both cases, the attacker can build a fork with more chainwork than the mainchain using less PoW than the protocol requires, causing the contract to accept a fraudulent chain as the canonical Bitcoin/Litecoin/Dogecoin chain. Downstream consumers of `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` would then verify proofs against a fraudulent mainchain. [6](#0-5) 

### Likelihood Explanation

**Medium.** The entry path is fully unprivileged: any NEAR account can call `submit_blocks` with adversarial headers. For Dogecoin the attack is most practical — the fork need only diverge two blocks before the target block, and the stale lookup corrupts difficulty for every subsequent fork block. For Bitcoin/Litecoin the attacker must mine a fork spanning a full 2016-block interval before the retarget boundary, which is expensive but within reach of a well-resourced adversary (e.g., a miner with significant hashrate). The developer's own TODO comment confirms awareness of the incorrect lookup.

### Recommendation

Replace `get_header_by_height(first_block_height)` with an ancestor traversal that walks backwards through `headers_pool` via `get_prev_header` starting from `prev_block_header` until reaching `first_block_height`. This mirrors the correct approach already used by `get_median_time_past`, which traverses the fork chain by hash rather than by mainchain height index: [7](#0-6) 

The fix ensures the difficulty calculation always uses the block that is the true ancestor of the block being validated, regardless of whether it

### Citations

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
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

**File:** contract/src/dogecoin.rs (L291-297)
```rust
    // TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
    let first_block_time = blocks_getter
        .get_header_by_height(height_first)
        .block_header
        .time;

    calculate_next_work_required(config, prev_block_header, i64::from(first_block_time))
```

**File:** contract/src/utils.rs (L10-26)
```rust
pub fn get_median_time_past(
    block_header: ExtendedHeader,
    prev_block_getter: &impl BlocksGetter,
) -> u32 {
    use btc_types::network::MEDIAN_TIME_SPAN;

    let mut median_time = [0u32; MEDIAN_TIME_SPAN];
    let mut current_header = block_header;

    for slot in &mut median_time {
        *slot = current_header.block_header.time;
        current_header = prev_block_getter.get_prev_header(&current_header.block_header);
    }

    median_time.sort_unstable();
    median_time[median_time.len() / 2]
}
```
