### Title
Storage Deposit Permanently Locked After GC and Reorg Header Removal — (`contract/src/lib.rs`)

### Summary

`submit_blocks` is `#[payable]` and collects NEAR storage deposits from relayers to cover the cost of persisting block headers. When `run_mainchain_gc` or `reorg_chain` later removes those headers from storage, the freed storage deposit is credited back to the contract's own account balance. No function exists anywhere in the contract to withdraw or redistribute those accumulated NEAR tokens. They are locked in the contract forever, mirroring the airdrop's unclaimed-token lockup bug class exactly.

### Finding Description

**Entry path:** Any caller (including an unprivileged relayer) invokes `submit_blocks` with an attached NEAR deposit.

**Storage deposit collection** — `submit_blocks` measures `diff_storage_usage` after inserting headers and charges the caller exactly `env::storage_byte_cost() * diff_storage_usage`: [1](#0-0) 

The excess is refunded to `predecessor_account_id`, but the exact required deposit is retained in the contract to back the newly occupied storage bytes.

**GC removal path** — `run_mainchain_gc` is called automatically inside every `submit_blocks` invocation. Once `amount_of_headers_we_store > gc_threshold`, it iterates over the oldest headers and calls `remove_block_header` + `mainchain_height_to_header.remove` for each: [2](#0-1) 

`remove_block_header` deletes entries from `mainchain_header_to_height` and `headers_pool`: [3](#0-2) 

On NEAR Protocol, removing storage keys returns the backing yoctoNEAR to the **contract's own account balance** — not to the original depositor. The contract has no `withdraw`, `recover_storage_deposit`, or equivalent function anywhere in its public API.

**Reorg removal path** — `reorg_chain` also calls `remove_block_header` for every mainchain block displaced by a heavier fork: [4](#0-3) 

The same lockup applies: the NEAR that backed those displaced headers is freed into the contract balance with no way out.

**No recovery function exists.** The entire public surface of `BtcLightClient` is:

- `init` / `migrate` — initialization only
- `submit_blocks` — payable, refunds only the per-call excess
- `run_mainchain_gc` — removes headers, no payment out
- `get_*` / `verify_*` — view functions [5](#0-4) 

Neither `near_plugins::Upgradable`, `Pausable`, nor `AccessControllable` adds a token-withdrawal method.

### Impact Explanation

**Impact: High.**

Every relayer that has ever paid a storage deposit to submit headers that were subsequently GC'd or reorg-displaced loses those NEAR tokens permanently. Over the operational lifetime of a live deployment (Bitcoin mainnet GC threshold is `52704` blocks ≈ 1 year of headers), the cumulative locked amount grows monotonically with every GC cycle. The tokens cannot be recovered by any on-chain action, upgrade path, or governance call without deploying entirely new contract code.

### Likelihood Explanation

**Likelihood: High.**

GC is not an edge case — it is triggered automatically inside every `submit_blocks` call once the stored chain exceeds `gc_threshold`: [6](#0-5) 

A production deployment with the recommended `gc_threshold = 52704` will begin GC after roughly one year of continuous relay, and will then GC on every subsequent block submission. Reorgs are also a routine part of Bitcoin operation. No attacker action is required; normal protocol operation is sufficient to trigger the lockup continuously.

### Recommendation

Add a privileged withdrawal function (callable only by `DAO` or a designated `TreasuryManager` role) that transfers the contract's free balance — i.e., `env::account_balance().saturating_sub(env::storage_byte_cost() * env::storage_usage())` — to a specified recipient. Alternatively, track per-relayer storage contributions in a `LookupMap<AccountId, NearToken>` and refund each relayer's proportional share when their headers are GC'd, mirroring the pattern used for the per-call excess refund already present in `submit_blocks`.

### Proof of Concept

1. Deploy the contract with `gc_threshold = 3`.
2. Relayer calls `submit_blocks([h1, h2, h3])` with a deposit of, say, 0.03 NEAR to cover storage. Contract balance increases by 0.03 NEAR.
3. Relayer calls `submit_blocks([h4])` with a small deposit. GC fires, removes `h1` from `headers_pool`, `mainchain_header_to_height`, and `mainchain_height_to_header`. Storage is freed; the backing deposit for `h1`'s storage returns to the contract's account balance.
4. Observe: contract balance is now higher than the storage obligation (`env::storage_byte_cost() * env::storage_usage()`). The surplus — the freed deposit for `h1` — is permanently trapped. No public function can move it out.
5. Repeat for every subsequent GC cycle. The trapped surplus grows with each removed header.

### Citations

**File:** contract/src/lib.rs (L126-417)
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

    pub fn get_last_block_header(&self) -> ExtendedHeader {
        self.headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
    }

    pub fn get_last_block_height(&self) -> u64 {
        self.headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST))
            .block_height
    }

    pub fn get_block_hash_by_height(&self, height: u64) -> Option<H256> {
        self.mainchain_height_to_header.get(&height)
    }

    #[allow(clippy::needless_pass_by_value)]
    pub fn get_height_by_block_hash(&self, blockhash: H256) -> Option<u64> {
        self.mainchain_header_to_height.get(&blockhash)
    }

    pub fn get_mainchain_size(&self) -> u64 {
        let tail = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let tip = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        tip.block_height - tail.block_height + 1
    }

    /// This method return n last blocks from the mainchain
    /// # Panics
    /// Cannot find a tip of main chain in a pool
    pub fn get_last_n_blocks_hashes(&self, skip: u64, limit: u64) -> Vec<H256> {
        let mut block_hashes = vec![];
        let tip_hash = &self.mainchain_tip_blockhash;
        let tip = self
            .headers_pool
            .get(tip_hash)
            .unwrap_or_else(|| env::panic_str("heaviest block should be recorded"));

        let min_block_height = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str("initial block should be recorded"))
            .block_height;

        let start_block_height =
            std::cmp::max(min_block_height, tip.block_height - limit - skip + 1);

        for height in start_block_height..=(tip.block_height - skip) {
            if let Some(block_hash) = self.mainchain_height_to_header.get(&height) {
                block_hashes.push(block_hash);
            }
        }

        block_hashes
    }

    /// Verifies that a transaction is included in a block at a given block height
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
    /// @param `tx_block_blockhash` block hash at which transacton is supposedly included
    /// @param `tx_index` index of transaction in the block's tx merkle tree
    /// @param `merkle_proof` merkle tree path (concatenated LE sha256 hashes) (does not contain initial `transaction_hash` and `merkle_root`)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if `tx_id` is at the claimed position in the block at the given blockhash, False otherwise
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
    /// Multiple cases
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
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

    /// Verifies that a transaction is included in a block at a given block height,
    /// with an additional coinbase merkle proof validation.
    /// This is needed to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param tx_id transaction identifier
    /// @param tx_block_blockhash block hash at which transaction is supposedly included
    /// @param tx_index index of transaction in the block's tx merkle tree
    /// @param merkle_proof merkle tree path (concatenated LE sha256 hashes) (does not contain initial transaction_hash and merkle_root)
    /// @param coinbase_tx_id coinbase transaction hash
    /// @param coinbase_merkle_proof merkle proof for the coinbase transaction (must have the same length as merkle_proof)
    /// @param confirmations how many confirmed blocks we want to have before the transaction is valid
    /// @return True if tx_id is at the claimed position in the block at the given blockhash, False otherwise
    ///
    /// # Panics
    /// - If `merkle_proof` and `coinbase_merkle_proof` have different lengths
    /// - If `tx_block_blockhash` is not found in the headers pool
    /// - If coinbase merkle proof does not match the block's merkle root
    /// - If the required number of confirmations exceeds the number of stored blocks
    /// - If the block does not belong to the current main chain
    /// - If there are not enough confirmed blocks
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }

    /// Public call to run GC on a mainchain.
    /// `batch_size` is how many block headers should be removed in the execution
    ///
    /// # Panics
    /// If initial blockheader or tip blockheader are not in a header pool
    #[pause(except(roles(Role::UnrestrictedRunGC)))]
    pub fn run_mainchain_gc(&mut self, batch_size: u64) {
        let initial_blockheader = self
            .headers_pool
            .get(&self.mainchain_initial_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let tip_blockheader = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));

        let amount_of_headers_we_store =
            tip_blockheader.block_height - initial_blockheader.block_height + 1;

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
    }
}
```

**File:** contract/src/lib.rs (L586-593)
```rust
            for height in (fork_tip_height + 1)..=last_main_chain_block_height {
                let current_main_chain_blockhash = self
                    .mainchain_height_to_header
                    .get(&height)
                    .unwrap_or_else(|| env::panic_str("cannot get a block"));
                self.remove_block_header(&current_main_chain_blockhash);
                self.mainchain_height_to_header.remove(&height);
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
