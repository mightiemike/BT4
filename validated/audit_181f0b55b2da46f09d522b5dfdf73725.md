### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Validation — (`contract/src/lib.rs`)

### Summary

The deprecated `verify_transaction_inclusion` function is still a live, publicly accessible NEAR entry point. Any unprivileged caller can invoke it directly, bypassing the coinbase Merkle proof check that `verify_transaction_inclusion_v2` was specifically introduced to enforce. This allows exploitation of the 64-byte transaction Merkle proof forgery vulnerability that v2 was designed to close.

### Finding Description

`verify_transaction_inclusion_v2` was introduced to mitigate the well-known 64-byte Merkle proof forgery attack (documented in the function's own docstring and linked to the BitMEX disclosure). The mitigation works by requiring a valid coinbase proof at position 0, which anchors the expected tree depth and prevents an attacker from presenting an internal Merkle node as a leaf transaction hash.

However, the original `verify_transaction_inclusion` function was not removed or access-restricted — it was only annotated with `#[deprecated]`. In Rust, `#[deprecated]` is a compiler lint warning for Rust callers; it has no effect on external NEAR contract calls. The function retains its `#[pause]` attribute (callable when not paused) and no role-based access control. [1](#0-0) 

The function body performs no coinbase proof validation: [2](#0-1) 

By contrast, `verify_transaction_inclusion_v2` enforces the coinbase check before delegating: [3](#0-2) 

The underlying `compute_root_from_merkle_proof` in `merkle-tools` accepts any `transaction_hash` and `transaction_position` without validating that the hash corresponds to an actual leaf node: [4](#0-3) 

An attacker who knows the Merkle tree structure of a real block can identify an internal node hash at depth D, construct a valid D-step proof path to the Merkle root, and submit it to `verify_transaction_inclusion` with `tx_id` set to that internal node hash. The function will return `true` for a transaction that does not exist.

### Impact Explanation

A downstream NEAR contract that calls `verify_transaction_inclusion` to gate a cross-chain action (e.g., releasing funds, minting tokens) will receive a forged `true` result. The attacker can claim that an arbitrary Bitcoin transaction was included in a real block without that transaction ever existing. This corrupts the core proof-verification guarantee of the light client.

### Likelihood Explanation

The attack requires only knowledge of a real Bitcoin block's Merkle tree structure (publicly available from any Bitcoin node or block explorer) and the ability to call a public NEAR contract method. No privileged role, private key, or social engineering is needed. The 64-byte forgery technique is well-documented and the entry point is unrestricted.

### Recommendation

Remove `verify_transaction_inclusion` entirely, or restrict it to a privileged role so it cannot be called by unprivileged external callers. If backward compatibility is required, at minimum apply the same coinbase proof check that `verify_transaction_inclusion_v2` performs before computing the Merkle root.

### Proof of Concept

1. Obtain a real Bitcoin block that is stored in the contract's `headers_pool` and confirmed on the mainchain.
2. From the block's full transaction list, identify an internal Merkle node hash `N` at depth D (e.g., `hash(tx0, tx1)` at depth 1).
3. Construct a D-step Merkle proof from `N` to the block's `merkle_root`.
4. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash, not a real transaction)
   - `tx_block_blockhash` = the real block hash
   - `tx_index` = the position corresponding to `N` in the tree at depth D
   - `merkle_proof` = the D-step proof constructed in step 3
   - `confirmations = 1`
5. The function returns `true`, falsely asserting that `N` is an included transaction in that block. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L283-323)
```rust
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
```

**File:** contract/src/lib.rs (L347-369)
```rust
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
```

**File:** merkle-tools/src/lib.rs (L34-52)
```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    let mut current_position = transaction_position;

    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
}
```
