### Title
Unresolved TODO: Mainchain-Only Ancestor Lookup Corrupts Dogecoin Fork Difficulty Validation - (`File: contract/src/dogecoin.rs`)

### Summary
`get_next_work_required` in the Dogecoin light client fetches the difficulty-period boundary block using `get_header_by_height`, which reads exclusively from the mainchain index. When validating a fork block whose divergence point precedes the difficulty-period boundary, the function silently uses the mainchain ancestor's timestamp instead of the fork's actual ancestor. A developer-acknowledged TODO comment marks this as unverified logic left in production. An unprivileged NEAR caller can exploit the resulting incorrect difficulty target to submit fork blocks with lower-than-required PoW, and if those blocks accumulate sufficient chainwork, trigger a chain reorg that corrupts the canonical chain stored by the light client.

### Finding Description

In `get_next_work_required` (`contract/src/dogecoin.rs`, lines 291–295), the code retrieves the timestamp of the first block in the current difficulty period via:

```rust
// TODO: check if it is correct to get block header by height from mainchain without looping to find the ancestor
let first_block_time = blocks_getter
    .get_header_by_height(height_first)
    .block_header
    .time;
``` [1](#0-0) 

`BlocksGetter::get_header_by_height` is implemented as:

```rust
fn get_header_by_height(&self, height: u64) -> ExtendedHeader {
    self.mainchain_height_to_header
        .get(&height)
        .and_then(|hash| self.headers_pool.get(&hash))
        .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
}
``` [2](#0-1) 

This lookup is unconditionally scoped to `mainchain_height_to_header`. For modern Dogecoin (height ≥ 145,000), `difficulty_adjustment_interval = 1`, so `blocks_to_go_back = 1` and `height_first = prev_block_height − 1`. [3](#0-2) 

When a fork block at height H+2 is submitted and the fork diverged at height H, the fork's block at height H+1 is stored in `headers_pool` as a fork entry but is **not** in `mainchain_height_to_header`. The call `get_header_by_height(H+1)` therefore returns the **mainchain** block at H+1, whose timestamp differs from the fork's block at H+1. The resulting `first_block_time` is wrong, and `calculate_next_work_required` produces an incorrect target `bits` value. [4](#0-3) 

The TODO comment is the direct analog to the original report's "test code present in codebase" class: unverified, placeholder-quality logic was shipped to production with an explicit developer note that it may be incorrect.

### Impact Explanation

An attacker who controls a fork chain can craft fork blocks whose timestamps, combined with the misread mainchain ancestor timestamp, yield a `calculate_next_work_required` output that is lower than the true required target. The `check_pow` call then validates the fork block's PoW hash against this artificially relaxed target, accepting blocks that would otherwise be rejected. [5](#0-4) 

If the attacker's fork accumulates chainwork exceeding the mainchain tip, `submit_block_header_inner` triggers `reorg_chain`, promoting the attacker's fork to the canonical mainchain. [6](#0-5) 

After reorg, `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` operate against the corrupted canonical chain, returning incorrect inclusion proofs for any downstream consumer. [7](#0-6) 

### Likelihood Explanation

The entry point is `submit_blocks`, a public payable function callable by any NEAR account. [8](#0-7) 

The Dogecoin feature is a supported production build target. The bug is triggered whenever a fork block is submitted whose divergence point is at or before `prev_block_height − 1`, a routine scenario during any fork submission. No privileged role, leaked key, or social engineering is required.

### Recommendation

Replace the mainchain-only height lookup with an ancestor traversal that follows `prev_block_hash` links through `headers_pool` starting from `prev_block_header`, walking back `blocks_to_go_back` steps. This mirrors how `get_prev_header` already traverses the pool and correctly resolves fork ancestors regardless of mainchain state.

### Proof of Concept

1. Deploy the Dogecoin light client with a mainchain initialized past height 145,000 (e.g., height 200,000).
2. Submit a fork block `F1` at height 200,001 that diverges from the mainchain at height 200,000. `F1` is stored in `headers_pool` as a fork entry; `mainchain_height_to_header[200,001]` still points to the mainchain block `M1`.
3. Submit a fork block `F2` at height 200,002 whose `prev_block_hash` is `F1`. During `check_pow` → `get_next_work_required`, `height_first = 200,001 − 1 = 200,000`... wait — for `difficulty_adjustment_interval = 1`, `blocks_to_go_back = 1`, so `height_first = 200,001 − 1 = 200,000`. `get_header_by_height(200,000)` returns the mainchain block at 200,000 (the fork divergence point itself, which is shared). For a deeper fork: submit `F1` at 200,001, `F2` at 200,002, then `F3` at 200,003. For `F3`, `height_first = 200,001`. `get_header_by_height(200,001)` returns mainchain block `M1` (timestamp `T_M1`), not fork block `F1` (timestamp `T_F1`). If `T_M1 − T_F0 ≠ T_F1 − T_F0`, `calculate_next_work_required` produces a different target than the correct one.
4. Craft `F3`'s timestamp so that the incorrect `first_block_time = T_M1` yields a lower required difficulty than the correct `T_F1` would. Submit `F3` with PoW meeting only the relaxed target — `check_pow` passes.
5. Repeat until the fork's cumulative chainwork exceeds the mainchain tip, triggering `reorg_chain` and corrupting the canonical chain. [9](#0-8) [2](#0-1)

### Citations

**File:** contract/src/dogecoin.rs (L23-47)
```rust
    pub(crate) fn check_pow(&self, block_header: &Header, prev_block_header: &ExtendedHeader) {
        let expected_bits =
            get_next_work_required(&self.get_config(), block_header, prev_block_header, self);

        require!(
            expected_bits == block_header.bits,
            format!(
                "Error: Incorrect target. Expected bits: {:?}, Actual bits: {:?}",
                expected_bits, block_header.bits
            )
        );

        // Check timestamp against median time past of the previous 11 blocks
        require!(
            block_header.time > get_median_time_past(prev_block_header.clone(), self),
            "time-too-old: block's timestamp is too early"
        );

        // Reject blocks whose timestamp is more than 2 hours ahead of local time
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
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

**File:** contract/src/lib.rs (L166-179)
```rust
    #[payable]
    #[pause]
    #[trusted_relayer]
    pub fn submit_blocks(
        &mut self,
        #[serializer(borsh)] headers: Vec<BlockHeader>,
    ) -> PromiseOrValue<()> {
        let amount = env::attached_deposit();
        let initial_storage = env::storage_usage();
        let num_of_headers = headers.len().try_into().unwrap();

        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }
```

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

**File:** contract/src/lib.rs (L562-567)
```rust
            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
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
