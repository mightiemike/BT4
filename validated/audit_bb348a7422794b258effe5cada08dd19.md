### Title
Stale Chain-Tip State Not Checked Before Transaction Inclusion Verification Allows Reorganized Transactions to Pass as Confirmed - (File: `contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` use the contract's stored `mainchain_tip_blockhash` and `mainchain_header_to_height` map to verify SPV proofs without checking whether the contract's chain state is fresh. If the relayer stops submitting headers and a Bitcoin reorganization occurs, a block that has been removed from the real Bitcoin main chain remains in the contract's `mainchain_header_to_height` map, causing the contract to return `true` for a transaction that is no longer confirmed on Bitcoin.

### Finding Description

Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` in `contract/src/lib.rs` resolve the current chain tip from `self.mainchain_tip_blockhash` and check block membership via `self.mainchain_header_to_height`, but neither function checks whether the contract's chain tip is temporally fresh. [1](#0-0) 

The `heaviest_block_header` used for the confirmation count is whatever was last submitted by the relayer. There is no check that `heaviest_block_header.block_header.time` (the Bitcoin timestamp of the tip) is within an acceptable window of `env::block_timestamp_ms()` (the current NEAR wall-clock time). The contract stores the Bitcoin block timestamp in every `LightHeader` and already uses it for PoW validation: [2](#0-1) 

but this check is only applied at *submission* time, not at *verification* time. Once a header is stored, its timestamp is never re-examined.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after the coinbase proof check, so it inherits the same gap: [3](#0-2) 

### Impact Explanation

Any bridge or protocol that calls `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` to gate an asset release treats a `true` return as proof that a Bitcoin transaction is irreversibly confirmed. If the relayer stops and a reorg occurs:

1. Block B (containing attacker transaction T) is in `mainchain_header_to_height` — it was submitted before the relayer stopped.
2. Bitcoin reorganizes: block B is no longer on the real main chain; T is unconfirmed.
3. The contract has no knowledge of the reorg because no new headers arrive.
4. The attacker calls `verify_transaction_inclusion(T, B, confirmations=0)`. The block is found in `mainchain_header_to_height`, the Merkle proof is valid, and the function returns `true`.
5. The consuming bridge releases funds for a transaction that no longer exists on Bitcoin.

The corrupted proof result is a direct consequence of the missing freshness check on `mainchain_tip_blockhash`.

### Likelihood Explanation

The relayer is a single off-chain process. A targeted DoS against the relayer's NEAR submission endpoint, a network partition, or a software crash can halt header submission. Bitcoin reorganizations of 1–2 blocks occur naturally; deeper reorgs are rare but have happened on smaller chains (Litecoin, Dogecoin, Zcash) that this contract also supports. An attacker who can halt the relayer (e.g., via resource exhaustion of the NEAR RPC endpoint the relayer uses) and simultaneously execute a short reorg on a lower-hashrate chain has a realistic path to exploit this. The `confirmations=0` option makes the attack easier because no depth requirement must be satisfied.

### Recommendation

In both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2`, after loading `heaviest_block_header`, add a staleness guard:

```rust
let tip_time = u64::from(heaviest_block_header.block_header.time);
let now_secs = env::block_timestamp_ms() / 1000;
// Reject if the chain tip is more than MAX_CHAIN_TIP_AGE seconds behind wall-clock time.
// A reasonable value is 2 * expected_block_time * gc_threshold_fraction,
// e.g. 7200 seconds (2 hours) for Bitcoin.
const MAX_CHAIN_TIP_AGE: u64 = 7200;
require!(
    now_secs.saturating_sub(tip_time) <= MAX_CHAIN_TIP_AGE,
    "chain tip is stale: relayer may have stopped"
);
```

The threshold should be parameterized per chain (Bitcoin 10-min blocks vs. Dogecoin 1-min blocks) and stored in `NetworkConfig` alongside `pow_target_spacing`. [4](#0-3) 

### Proof of Concept

1. Deploy the contract (Bitcoin feature) with a genesis at height H and `gc_threshold = 1000`.
2. Relayer submits blocks H through H+5; block H+3 contains attacker transaction T. Contract's `mainchain_tip_blockhash` points to H+5.
3. Attacker halts the relayer (DoS or process kill).
4. Bitcoin reorganizes: blocks H+3 through H+5 are replaced by an alternate chain. Transaction T is no longer confirmed.
5. Attacker calls `verify_transaction_inclusion` with `tx_id = T`, `tx_block_blockhash = hash(H+3)`, `confirmations = 0`.
6. Contract looks up `hash(H+3)` in `mainchain_header_to_height` — it is present (the reorg was never submitted). Merkle proof validates against the stored `merkle_root`. Function returns `true`.
7. Consuming bridge contract releases funds. [5](#0-4)

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

**File:** contract/src/lib.rs (L367-369)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** contract/src/bitcoin.rs (L34-39)
```rust
        // Check timestamp
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
```

**File:** btc-types/src/network.rs (L152-161)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Copy, Debug)]
pub struct NetworkConfig {
    pub pow_target_timespan: i64,
    pub difficulty_adjustment_interval: u64,
    pub proof_of_work_limit_bits: u32,
    pub pow_target_spacing: u32,
    pub pow_allow_min_difficulty_blocks: bool,
    pub pow_limit: U256,
}
```
