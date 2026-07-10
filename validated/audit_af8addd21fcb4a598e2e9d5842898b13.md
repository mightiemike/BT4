### Title
`skip_pow_verification` Cannot Be Changed After Initialization — (`File: contract/src/lib.rs`)

### Summary
The `skip_pow_verification` flag is set once during `init()` and stored permanently in contract state. No privileged setter function exists to update it. If the contract is deployed with `skip_pow_verification = true` (even accidentally), PoW validation is permanently bypassed for all `submit_blocks` calls, allowing any unprivileged relayer to corrupt the canonical chain state.

### Finding Description
`BtcLightClient` stores `skip_pow_verification` as a persistent field in its Borsh-serialized state: [1](#0-0) 

It is assigned exactly once, inside `init()`: [2](#0-1) 

The flag is then read on every `submit_block_header` call to decide whether PoW is checked: [3](#0-2) 

A search across all contract source files (`contract/src/*.rs`) finds **zero** functions named `set_*`, `update_*`, or `change_*`. There is no admin-gated method to flip `skip_pow_verification` from `true` to `false` after deployment. The `migrate()` path exists but only restructures the Borsh layout; it does not expose a way to change the flag value: [4](#0-3) 

The `InitArgs` struct confirms `skip_pow_verification` is a deploy-time-only parameter with no post-init update path: [5](#0-4) 

### Impact Explanation
When `skip_pow_verification = true`, the entire PoW check block is skipped: [6](#0-5) 

Any unprivileged `submit_blocks` caller can then submit headers whose hash does not satisfy the declared `bits` target. These headers are accepted into `headers_pool`, can become the `mainchain_tip_blockhash`, and are recorded in `mainchain_height_to_header` / `mainchain_header_to_height`. Downstream callers of `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` will verify Merkle proofs against a chain whose headers were never validated against Bitcoin's PoW rules — producing false-positive inclusion results for fabricated transactions.

### Likelihood Explanation
The precondition is that the contract is deployed with `skip_pow_verification = true`. The code comment explicitly warns this flag is for testing only: [7](#0-6) 

Accidental production deployment with the testing flag is a realistic operational mistake. Once deployed, there is no recovery path short of a full contract upgrade + state migration, which is a heavyweight process compared to a single privileged setter call. The absence of a setter makes the window of exposure permanent rather than correctable.

### Recommendation
Add a privileged setter function (gated behind `Role::DAO` or `Role::PauseManager`) to update `skip_pow_verification` and `gc_threshold` post-deployment:

```rust
pub fn set_skip_pow_verification(&mut self, value: bool) {
    // require DAO role
    self.skip_pow_verification = value;
}

pub fn set_gc_threshold(&mut self, value: u64) {
    // require DAO role
    self.gc_threshold = value;
}
```

This mirrors the recommendation in the original report: add an update function so that critical operational parameters do not require a full contract redeployment to correct.

### Proof of Concept
1. Deploy `BtcLightClient` with `InitArgs { skip_pow_verification: true, ... }`.
2. As any unprivileged NEAR account, call `submit_blocks` with a `BlockHeader` whose `nonce` is arbitrary (i.e., the hash does not satisfy `bits`).
3. Observe the block is accepted: `get_last_block_header()` returns the invalid header as the new chain tip.
4. Call `verify_transaction_inclusion_v2` with a fabricated Merkle proof rooted in the invalid header — it returns `true`.
5. Attempt to correct the situation: no `set_skip_pow_verification` function exists; the only remediation is a full contract upgrade cycle.

### Citations

**File:** contract/src/lib.rs (L96-118)
```rust
pub struct BtcLightClient {
    // A pair of lookup maps that allows to find header by height and height by header
    mainchain_height_to_header: LookupMap<u64, H256>,
    mainchain_header_to_height: LookupMap<H256, u64>,

    // Block with the highest chainWork, i.e., blockchain tip, you can find latest height inside of it
    mainchain_tip_blockhash: H256,

    // The oldest block in main chain we store
    mainchain_initial_blockhash: H256,

    // Mapping of block hashes to block headers (ALL ever submitted, i.e., incl. forks)
    headers_pool: LookupMap<H256, ExtendedHeader>,

    // If we should run all the block checks or not
    skip_pow_verification: bool,

    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,

    // Network type Mainnet/Testnet
    network: Network,
}
```

**File:** contract/src/lib.rs (L127-134)
```rust
    /// Recommended initialization parameters:
    /// * `genesis_block_height % difficulty_adjustment_interval == 0`: The genesis block height must be divisible by `difficulty_adjustment_interval` to align with difficulty adjustment cycles.
    /// * The `genesis_block` must be at least 144 blocks earlier than the last block. 144 is the approximate number of blocks generated in one day.
    /// * `skip_pow_verification = false`: Should be set to `false` for standard use. Set to `true` only for testing purposes.
    /// * `gc_threshold = 52704`: This is the approximate number of blocks generated in a year.
    #[init]
    #[private]
    #[must_use]
```

**File:** contract/src/lib.rs (L135-145)
```rust
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

**File:** contract/src/lib.rs (L726-751)
```rust
        pub fn migrate() -> Self {
            let raw_state = env::storage_read(b"STATE")
                .unwrap_or_else(|| env::panic_str("contract state not found"));

            if let Ok(state) = <Self as BorshDeserialize>::try_from_slice(&raw_state) {
                log!("state is already in the current layout");
                return state;
            }

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

            env::panic_str("contract state matches no known layout")
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
