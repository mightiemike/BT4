### Title
Truncated Merkle Proof Bypasses 64-Byte Forgery Protection in `verify_transaction_inclusion_v2` - (File: `contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion_v2` is intended to prevent the 64-byte Merkle proof forgery attack by requiring a coinbase proof of the same length as the transaction proof. However, because neither proof length is validated against the actual tree depth, an attacker can supply both proofs shortened by the same number of levels, satisfying all checks while presenting an internal Merkle tree node as a valid transaction hash.

### Finding Description

The external report's vulnerability class is **lack of size/length validation on structured binary data**, where truncated input bypasses a security check because the system implicitly accepts the shorter form. The analog here is that `verify_transaction_inclusion_v2` accepts Merkle proofs of any length without verifying that the length matches the actual depth of the Merkle tree for the claimed `tx_index`.

`verify_transaction_inclusion_v2` enforces only one structural constraint on proof length: [1](#0-0) 

```rust
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    "Coinbase merkle proof and transaction merkle proof should have the same length"
);
```

It then verifies the coinbase proof against the stored merkle root: [2](#0-1) 

And delegates the transaction proof check to the deprecated v1 function: [3](#0-2) 

The underlying `compute_root_from_merkle_proof` in `merkle-tools` iterates over however many proof elements are provided, with no check that the count matches the tree depth: [4](#0-3) 

For a block whose Merkle tree has depth D > 1, let L = left child of the root and R′ = right child of the root (both internal nodes, publicly computable from the block). An attacker supplies:

- `coinbase_tx_id = L`, `coinbase_merkle_proof = [R′]`
- `tx_id = R′`, `tx_index = 1`, `merkle_proof = [L]`

Coinbase check: `hash(L, R′) == merkle_root` ✓  
Transaction check: `hash(L, R′) == merkle_root` ✓ (position 1 → sibling-first ordering)

Both checks pass with proofs of length 1 instead of the actual depth D. The function returns `true` for `tx_id = R′`, which is an internal node, not a real transaction.

### Impact Explanation

Any NEAR contract that calls `verify_transaction_inclusion_v2` to gate a privileged action (e.g., a cross-chain bridge releasing funds, a custody contract unlocking assets) can be deceived into accepting a fabricated transaction inclusion proof. The attacker does not need to forge a Bitcoin block or break any cryptographic primitive — all required values (L, R′) are publicly readable from the Bitcoin blockchain.

### Likelihood Explanation

`verify_transaction_inclusion_v2` is a public, unpermissioned NEAR contract method (gated only by the `#[pause]` flag, not by `#[trusted_relayer]`). Any NEAR account can call it with crafted arguments. The required internal node values are computable from public block data. Every Bitcoin block with more than one transaction has a tree depth > 1, so the precondition is satisfied by virtually every real block.

### Recommendation

Validate that the proof length is consistent with the claimed `tx_index`. The minimum required proof depth for a transaction at position `tx_index` is `ceil(log2(tx_index + 1))`. A stricter fix is to require the caller to supply the total transaction count and enforce `merkle_proof.len() == ceil(log2(tx_count))`. Additionally, the coinbase proof check should verify that `coinbase_tx_id` is the actual coinbase transaction (e.g., by checking the raw transaction bytes), not merely that it hashes to the root with the provided sibling — otherwise a shortened proof using internal nodes will always satisfy the check.

### Proof of Concept

Given a mainchain block B with merkle root R, left subtree hash L, and right subtree hash R′:

```rust
// All values are publicly computable from Bitcoin block data
let args = ProofArgsV2 {
    tx_id: R_prime,                    // internal node, not a real tx
    tx_block_blockhash: B,
    tx_index: 1,
    merkle_proof: vec![L],             // length 1, tree depth may be >> 1
    coinbase_tx_id: L,                 // internal node, not the real coinbase tx
    coinbase_merkle_proof: vec![R_prime], // length 1, same as merkle_proof
    confirmations: 1,
};
// verify_transaction_inclusion_v2(args) returns true
// because hash(L, R') == R for both the coinbase and tx checks
```

The `compute_root_from_merkle_proof` function processes exactly the elements provided: [5](#0-4) 

With `transaction_position = 1` and `merkle_proof = [L]`: the single iteration computes `hash(L, R′)` which equals the stored `merkle_root`, so the function returns `true`. The coinbase check with `coinbase_tx_id = L` and `coinbase_merkle_proof = [R′]` computes `hash(L, R′)` identically. No size check exists to detect that both proofs are one level shorter than the actual tree depth. [6](#0-5)

### Citations

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
