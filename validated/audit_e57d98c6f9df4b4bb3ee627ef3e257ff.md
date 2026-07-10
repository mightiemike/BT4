### Title
No Mechanism to Reclaim Storage Deposits Locked by Orphaned Fork Headers - (File: `contract/src/lib.rs`)

### Summary
The `BtcLightClient` contract permanently locks NEAR storage deposits paid for fork (orphan) block headers. `store_fork_header` inserts headers into `headers_pool` only, and neither `run_mainchain_gc` nor any other function ever removes fork headers that do not win a reorg. There is no withdrawal function to reclaim the freed or stranded NEAR tokens. This is a direct analog to the "missing claim mechanism" class: value accrues inside the contract with no retrieval path.

### Finding Description

`submit_blocks` is `#[payable]` and charges the caller a storage deposit proportional to the net increase in `env::storage_usage()` after processing all submitted headers. [1](#0-0) 

When a submitted header does not extend the current mainchain tip, `submit_block_header_inner` routes it to `store_fork_header`: [2](#0-1) 

`store_fork_header` writes only to `headers_pool` — it does not touch either mainchain index map: [3](#0-2) 

`run_mainchain_gc` iterates exclusively over `mainchain_height_to_header` and calls `remove_block_header` only for mainchain entries. Fork entries in `headers_pool` are never visited: [4](#0-3) 

`remove_block_header` itself only touches `mainchain_header_to_height` and `headers_pool`, and is only ever called from the mainchain GC path or from `reorg_chain` for displaced mainchain blocks — never for losing fork blocks: [5](#0-4) 

The project documentation confirms this is by design: *"Only mainchain blocks are pruned; fork/sidechain blocks remain."* [6](#0-5) 

There is no `withdraw`, `remove_fork_header`, or any other function in the contract's public API that can remove orphaned fork headers or transfer freed NEAR balance to any recipient. The entire public API consists of `submit_blocks`, `run_mainchain_gc`, the read-only view functions, and `migrate` — none of which reclaim fork-header storage. [7](#0-6) 

A second, related accumulation path exists: when `run_mainchain_gc` removes mainchain blocks, the storage bytes freed cause the contract's locked balance to convert to free balance. Because there is no withdrawal function, this freed NEAR also accumulates in the contract account with no retrieval path.

### Impact Explanation

Every fork block submitted by a legitimate relayer during normal Bitcoin operation (orphan races, competing miners, deliberate fork probing) permanently locks the storage deposit paid for that block. Over the lifetime of a deployment with `gc_threshold = 52704` (one year of blocks), the cumulative locked NEAR from orphaned fork headers and freed GC deposits can be substantial. The original depositors — relayers — cannot recover these funds. The contract's balance grows monotonically from both sources with no mechanism to distribute or reinvest the value, directly mirroring the "rewards locked in the protocol" impact described in the reference report.

### Likelihood Explanation

Bitcoin forks (orphan blocks) occur in every difficulty epoch. Any relayer operating the off-chain sync service will routinely submit fork headers as part of normal chain-following logic. The `relayer/src/synchronizer.rs` sync loop is designed to submit headers including those on competing forks to resolve reorgs. This is not an edge case; it is the expected steady-state behavior of the protocol.

### Recommendation

1. Track the submitter of each fork header (e.g., store `submitter: AccountId` alongside the `ExtendedHeader` in `headers_pool`).
2. Add a `remove_fork_header(blockhash: H256)` function that verifies the header is not on the current mainchain, removes it from `headers_pool`, and refunds the freed storage deposit to the original submitter.
3. Add a privileged `withdraw_excess_balance(amount: NearToken, recipient: AccountId)` function (gated to `Role::DAO`) to reclaim freed GC deposits that have accumulated as free contract balance.
4. Write integration tests verifying that fork-header storage deposits are fully recoverable after the fork loses.

### Proof of Concept

1. Deploy the contract with `gc_threshold = 52704`.
2. As a trusted relayer, call `submit_blocks` with a valid fork block `F1` (a block whose `prev_block_hash` points to a block already in `headers_pool` but is not the current mainchain tip). Pay the required storage deposit `D`.
3. Continue submitting mainchain blocks so the fork never accumulates enough chainwork to trigger `reorg_chain`.
4. Call `run_mainchain_gc` repeatedly until the mainchain has been pruned well past the fork point.
5. Observe: `F1` remains in `headers_pool` (query `get_block_hash_by_height` — it returns `None` for `F1`'s height on the mainchain, yet `F1` still occupies storage). The deposit `D` is irrecoverable. No public function exists to remove `F1` or return `D` to the relayer. [8](#0-7) [3](#0-2)

### Citations

**File:** contract/src/lib.rs (L126-198)
```rust
impl BtcLightClient {
    /// Recommended initialization parameters:
    /// * `genesis_block_height % difficulty_adjustment_interval == 0`: The genesis block height must be divisible by `difficulty_adjustment_interval` to align with difficulty adjustment cycles.
    /// * The `genesis_block` must be at least 144 blocks earlier than the last block. 144 is the approximate number of blocks generated in one day.
    /// * `skip_pow_verification = false`: Should be set to `false` for standard use. Set to `true` only for testing purposes.
    /// * `gc_threshold = 52704`: This is the approximate number of blocks generated in a year.
    #[init]
    #[private]
    #[must_use]
    pub fn init(args: InitArgs) -> Self {
        let mut contract = Self {
            mainchain_height_to_header: LookupMap::new(StorageKey::MainchainHeightToHeader),
            mainchain_header_to_height: LookupMap::new(StorageKey::MainchainHeaderToHeight),
            headers_pool: LookupMap::new(StorageKey::HeadersPool),
            mainchain_initial_blockhash: H256::default(),
            mainchain_tip_blockhash: H256::default(),
            skip_pow_verification: args.skip_pow_verification,
            gc_threshold: args.gc_threshold,
            network: args.network,
        };

        // Make the contract itself super admin. This allows us to grant any role in the
        // constructor.
        near_sdk::require!(
            contract.acl_init_super_admin(env::current_account_id()),
            "Failed to initialize super admin",
        );

        contract.init_genesis(
            &args.genesis_block_hash,
            args.genesis_block_height,
            args.submit_blocks,
        );

        contract
    }

    /// This method submits provided headers
    /// # Panics
    /// Cannot parse headers len as u64
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

        self.run_mainchain_gc(num_of_headers);
        let diff_storage_usage = env::storage_usage().saturating_sub(initial_storage);
        let required_deposit = env::storage_byte_cost().saturating_mul(diff_storage_usage.into());

        require!(
            amount >= required_deposit,
            format!("Required deposit {}", required_deposit)
        );

        let refund = amount.saturating_sub(required_deposit);
        if refund > NearToken::from_near(0) {
            Promise::new(env::predecessor_account_id())
                .transfer(refund)
                .into()
        } else {
            PromiseOrValue::Value(())
        }
    }
```

**File:** contract/src/lib.rs (L391-415)
```rust
        if amount_of_headers_we_store > self.gc_threshold {
            let total_amount_to_remove = amount_of_headers_we_store - self.gc_threshold;
            let selected_amount_to_remove = std::cmp::min(total_amount_to_remove, batch_size);

            let start_removal_height = initial_blockheader.block_height;
            let end_removal_height = initial_blockheader.block_height + selected_amount_to_remove;
            env::log_str(&format!(
                "Num of blocks to remove {selected_amount_to_remove}"
            ));

            for height in start_removal_height..end_removal_height {
                let blockhash = &self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

                self.remove_block_header(blockhash);
                self.mainchain_height_to_header.remove(&height);
            }

            self.mainchain_initial_blockhash = self
                .mainchain_height_to_header
                .get(&end_removal_height)
                .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        }
```

**File:** contract/src/lib.rs (L549-566)
```rust
        } else {
            log!("Block {}: saving to fork", current_header.block_hash);
            // Fork submission
            let main_chain_tip_header = self
                .headers_pool
                .get(&self.mainchain_tip_blockhash)
                .unwrap_or_else(|| env::panic_str("tip should be in a header pool"));

            let last_main_chain_block_height = main_chain_tip_header.block_height;
            let total_main_chain_chainwork = main_chain_tip_header.chain_work;

            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
```

**File:** contract/src/lib.rs (L658-662)
```rust
    /// Remove block header and meta information
    fn remove_block_header(&mut self, header_block_hash: &H256) {
        self.mainchain_header_to_height.remove(header_block_hash);
        self.headers_pool.remove(header_block_hash);
    }
```

**File:** contract/src/lib.rs (L664-667)
```rust
    /// Stores and handles fork submissions
    fn store_fork_header(&mut self, header: &ExtendedHeader) {
        self.headers_pool.insert(&header.block_hash, header);
    }
```

**File:** contract/CLAUDE.md (L39-39)
```markdown
- **gc_threshold**: max number of mainchain blocks to keep in storage. When the mainchain grows beyond this, the oldest mainchain blocks are pruned. GC runs automatically during `submit_blocks()` (with `batch_size` = number of submitted headers) and can also be triggered manually via `run_mainchain_gc(batch_size)`. Only mainchain blocks are deleted; fork/sidechain blocks are not affected
```
