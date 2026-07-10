### Title
`gc_threshold = 0` in `init` forces zero-confirmation SPV proofs, breaking the confirmation security model — (File: `contract/src/lib.rs`)

### Summary

The `init` function accepts `gc_threshold` from `InitArgs` with no lower-bound validation. When `gc_threshold` is set to `0`, the guard inside `verify_transaction_inclusion` that bounds the caller-supplied `confirmations` value to `<= gc_threshold` forces every SPV proof call to use `confirmations = 0`. This means any transaction at the chain tip — with zero blocks on top — is treated as fully confirmed, completely defeating the protocol's double-spend protection.

### Finding Description

`init` stores `args.gc_threshold` directly into contract state without any `require!` that it is greater than zero: [1](#0-0) 

`gc_threshold` is then used as a hard ceiling on the `confirmations` argument in `verify_transaction_inclusion`: [2](#0-1) 

When `gc_threshold = 0`, the only value that satisfies `args.confirmations <= 0` is `confirmations = 0`. The subsequent depth check then becomes:

```
(heaviest_block_height - target_block_height + 1) >= 0
```

which is trivially true for every block in the chain, including the tip itself. [3](#0-2) 

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its coinbase check, so it is equally affected. [4](#0-3) 

The `InitArgs` struct imposes no constraint on `gc_threshold`: [5](#0-4) 

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` is forced to supply `confirmations = 0`. A transaction included in the current chain tip — with no blocks built on top — is returned as verified. An attacker can broadcast a transaction, obtain a `true` SPV proof immediately, trigger an action in a consuming contract (e.g., a bridge release), and then allow the block to be reorganized away. The confirmation requirement, which is the sole on-chain defense against this class of double-spend, is silently nullified.

### Likelihood Explanation

Initialization is `#[private]`, so only the deploying account can trigger it. The risk mirrors the original report: the deployer could set `gc_threshold = 0` by mistake (the recommended value of `52704` is documented only in a comment, not enforced), or a malicious deployer could do so deliberately to weaken the bridge's security guarantees for downstream consumers who trust the contract's confirmation semantics. [6](#0-5) 

### Recommendation

Add a `require!` in `init` before storing `gc_threshold`:

```rust
require!(args.gc_threshold > 0, "gc_threshold must be greater than 0");
```

Optionally enforce a minimum meaningful value (e.g., `>= MEDIAN_TIME_SPAN`) to ensure the GC window is always large enough to support the minimum confirmation depth that consuming contracts are expected to request.

### Proof of Concept

1. Deploy the contract with `InitArgs { gc_threshold: 0, ... }`.
2. Submit any valid block header via `submit_blocks`.
3. Call `verify_transaction_inclusion` with `confirmations: 0` for a transaction in the just-submitted tip block.
4. The call returns `true` — the transaction is declared confirmed with zero blocks on top.
5. Any consuming contract that relies on this result to release funds or record state is now vulnerable to a reorg-based double-spend. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L127-131)
```rust
    /// Recommended initialization parameters:
    /// * `genesis_block_height % difficulty_adjustment_interval == 0`: The genesis block height must be divisible by `difficulty_adjustment_interval` to align with difficulty adjustment cycles.
    /// * The `genesis_block` must be at least 144 blocks earlier than the last block. 144 is the approximate number of blocks generated in one day.
    /// * `skip_pow_verification = false`: Should be set to `false` for standard use. Set to `true` only for testing purposes.
    /// * `gc_threshold = 52704`: This is the approximate number of blocks generated in a year.
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

**File:** contract/src/lib.rs (L288-308)
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
```

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
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
