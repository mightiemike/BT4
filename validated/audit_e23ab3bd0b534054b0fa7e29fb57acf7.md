### Title
Unvalidated `skip_pow_verification` Flag Set at Initialization Permanently Disables PoW Verification With No Recovery Path - (`contract/src/lib.rs`)

### Summary

The `BtcLightClient` contract accepts a `skip_pow_verification` boolean at initialization with no validation and no mechanism to change it post-deployment. If set to `true`, every subsequent `submit_blocks` call bypasses all proof-of-work checks, allowing a relayer to inject arbitrary headers, corrupt the canonical chain, and cause `verify_transaction_inclusion` to return `true` for fabricated transactions.

### Finding Description

`InitArgs` carries a `skip_pow_verification` field that is copied verbatim into contract state during `init()`: [1](#0-0) 

No assertion, warning, or guard prevents `skip_pow_verification: true` from being used in a production deployment. The flag is then read on every header submission: [2](#0-1) 

When the flag is `true`, the entire PoW branch inside `submit_block_header` is skipped: [3](#0-2) 

This means neither the difficulty target check (`check_target` → `check_pow`) nor the hash-vs-target comparison is performed. For Dogecoin the same flag is forwarded and the AuxPoW path is also skipped: [4](#0-3) 

There is no setter for `skip_pow_verification` anywhere in the contract. The `migrate()` path preserves whatever value was stored: [5](#0-4) 

The `InitArgs` struct itself imposes no constraint on the field: [6](#0-5) 

### Impact Explanation

With `skip_pow_verification = true` permanently baked into state, any caller that passes the `#[trusted_relayer]` gate on `submit_blocks` can submit a sequence of headers with arbitrary `bits`, arbitrary hashes, and zero real mining work. Because `submit_block_header_inner` promotes a fork whenever its `chain_work` exceeds the current tip, an attacker can craft a chain with inflated `bits` values (low difficulty → high `work_from_bits` return) and trigger a reorg to a fully fabricated canonical chain. Once that chain is canonical, `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` will confirm inclusion of transactions that never existed on the real Bitcoin network, directly deceiving any downstream NEAR contract that consumes the verification result. [7](#0-6) 

### Likelihood Explanation

The documentation comment on `init` explicitly states `skip_pow_verification = false` is the correct production value and `true` is "only for testing purposes," yet the code enforces nothing: [8](#0-7) 

A misconfigured deployment (copy-paste from a test script, CI/CD pipeline error, or a deployer who misreads the flag semantics) permanently locks the contract into an insecure state with no on-chain recovery. Because the flag cannot be changed after deployment, the only remediation is a full redeployment and re-initialization, which requires coordinated off-chain action and may not be possible if the contract is already in use.

### Recommendation

1. **Enforce the flag at initialization**: Add a `require!(!args.skip_pow_verification, "skip_pow_verification must be false in production")` guard inside `init()`, or remove the field from `InitArgs` entirely and introduce a separate, role-gated method to toggle it only for explicitly authorized test deployments.
2. **Add a privileged setter**: Expose a DAO/PauseManager-gated function to update `skip_pow_verification` so that a misconfigured deployment can be corrected without a full redeployment.
3. **Emit an on-chain warning**: At minimum, log a prominent warning when the contract is initialized with `skip_pow_verification = true`.

### Proof of Concept

1. Deploy `BtcLightClient` with `InitArgs { skip_pow_verification: true, ... }`.
2. Register as a trusted relayer (or hold `Role::UnrestrictedSubmitBlocks`).
3. Call `submit_blocks` with a vector of headers where:
   - `bits = 0x207fffff` (near-zero difficulty, maximum `work_from_bits` value)
   - `prev_block_hash` chains from the genesis block
   - `time` values satisfy the MTP check (monotonically increasing by ≥1 second)
   - `version ≥ 4`
4. Because `skip_pow_verification = true`, `check_target` and the hash-vs-target comparison are never called; headers are accepted unconditionally.
5. After enough headers, `chain_work` of the fabricated fork exceeds the real tip, triggering `reorg_chain` and replacing the canonical chain.
6. Call `verify_transaction_inclusion_v2` with a fabricated `tx_id` and a valid Merkle proof against one of the injected headers' `merkle_root`; the function returns `true`.

### Citations

**File:** contract/src/lib.rs (L127-145)
```rust
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
```

**File:** contract/src/lib.rs (L177-179)
```rust
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

**File:** contract/src/lib.rs (L517-528)
```rust
        if !skip_pow_verification {
            self.check_target(&header, &prev_block_header);

            let pow_hash = header.block_hash_pow();
            // Check if the block hash is less than or equal to the target
            require!(
                U256::from_le_bytes(&pow_hash.0) <= target_from_bits(header.bits),
                format!("block should have correct pow")
            );
        }

        self.submit_block_header_inner(current_header, &prev_block_header);
```

**File:** contract/src/lib.rs (L735-747)
```rust
            if let Ok(old_state) = BtcLightClientV2::try_from_slice(&raw_state) {
                log!("migrating state from the V2 layout");
                return Self {
                    mainchain_height_to_header: old_state.mainchain_height_to_header,
                    mainchain_header_to_height: old_state.mainchain_header_to_height,
                    mainchain_tip_blockhash: old_state.mainchain_tip_blockhash,
                    mainchain_initial_blockhash: old_state.mainchain_initial_blockhash,
                    headers_pool: old_state.headers_pool,
                    skip_pow_verification: old_state.skip_pow_verification,
                    gc_threshold: old_state.gc_threshold,
                    network: old_state.network,
                };
            }
```

**File:** contract/src/dogecoin.rs (L176-189)
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
        }
```

**File:** btc-types/src/contract_args.rs (L7-14)
```rust
pub struct InitArgs {
    pub genesis_block_hash: H256,
    pub genesis_block_height: u64,
    pub skip_pow_verification: bool,
    pub gc_threshold: u64,
    pub network: Network,
    pub submit_blocks: Vec<Header>,
}
```
