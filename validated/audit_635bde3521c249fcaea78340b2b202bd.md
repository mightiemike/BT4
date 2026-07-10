### Title
Zero-Confirmation Bypass in `verify_transaction_inclusion` — (`File: contract/src/lib.rs`)

### Summary
The `verify_transaction_inclusion` (and transitively `verify_transaction_inclusion_v2`) functions accept a caller-supplied `confirmations` parameter with no minimum bound. Any unprivileged NEAR caller can pass `confirmations = 0`, completely bypassing the reorg-protection guarantee the parameter is designed to enforce. This is a direct analog to the `minAmountOut == 0` class of vulnerability: a protection parameter that is supposed to enforce a minimum threshold can be set to zero by the caller, nullifying the protection.

### Finding Description
In `contract/src/lib.rs`, `verify_transaction_inclusion` enforces only an upper bound on `confirmations`:

```rust
require!(
    args.confirmations <= self.gc_threshold,
    "The required number of confirmations exceeds the number of blocks stored in memory"
);
``` [1](#0-0) 

There is no lower bound. The subsequent confirmation check is:

```rust
require!(
    (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
        >= args.confirmations,
    "Not enough blocks confirmed"
);
``` [2](#0-1) 

When `args.confirmations == 0`, the right-hand side is `0`, and the left-hand side is a `u64` that is always `>= 0`. The check is trivially satisfied for **any** block in the main chain, including the chain tip itself (zero confirmations). The function then proceeds to verify the Merkle proof and returns `true`.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its own coinbase proof check, so it inherits the same flaw:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [3](#0-2) 

The `ProofArgs` struct accepts `confirmations: u64` with no validation at the type level: [4](#0-3) 

### Impact Explanation
Any protocol that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to gate fund releases or cross-chain actions can be exploited. An attacker passes `confirmations = 0`, receives `true` for a transaction in the current chain tip, and acts on that result before a reorg removes the block. The confirmation count is the sole on-chain reorg-protection mechanism; setting it to zero eliminates all protection.

### Likelihood Explanation
The functions are public and callable by any NEAR account when the contract is not paused. No privileged role is required. The only prerequisite is that the target block is already in the main chain, which is the normal operating state. A consumer contract that does not enforce a minimum `confirmations` value in its own call is immediately exploitable.

### Recommendation
Add a minimum confirmation requirement. Either:
1. Enforce a protocol-level minimum (e.g., `require!(args.confirmations >= MIN_CONFIRMATIONS, ...)`) inside `verify_transaction_inclusion`, or
2. Reject `confirmations == 0` explicitly, since zero confirmations provides no reorg protection by definition.

### Proof of Concept
1. Relayer submits block N (the new chain tip) via `submit_blocks`.
2. Attacker immediately calls `verify_transaction_inclusion` with `tx_block_blockhash = block_N_hash`, a valid Merkle proof for a transaction in block N, and `confirmations = 0`.
3. The upper-bound check passes (`0 <= gc_threshold`). The confirmation depth check evaluates `(N - N) + 1 >= 0` → `1 >= 0` → `true`. The Merkle proof is valid. The function returns `true`.
4. A chain reorganization removes block N. The attacker has already used the `true` result to claim funds from a dependent protocol.
5. The same attack applies via `verify_transaction_inclusion_v2` by additionally supplying a valid coinbase Merkle proof. [5](#0-4)

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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```

**File:** btc-types/src/contract_args.rs (L16-24)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
