[File: 'btc-types/src/lib.rs -> Scope: Critical. Merkle proof or transaction inclusion validation flaw that returns true for a nonexistent transaction, wrong index, wrong block, forged internal node, or otherwise invalid inclusion claim.'] [Function: compute_root_from_merkle_proof / verify_transaction_inclusion] Can an unprivileged proof submitter supply tx_index larger than the actual number of transactions in the block (e.g., tx_index=5 for a 4-tx tree) with a merkle_proof of the correct tree depth, under the precondition that no bounds check on tx_index against tx_count exists in either verify_transaction_inclusion or compute_root_from_merkle_proof, triggering compute_root_from_merkle_proof to derive left/right position bits from the oversized index (current_position % 2, /= 2) and produce a root compared against the stored merkle_root, violating the invariant that tx_index must be within [0, tx_count-1] for the claimed block, causing scoped impact: an attacker who finds a tx_id and proof such that the wrong position bits produce the correct root could verify a nonexistent transaction at an out-of-range index? Proof idea: for a

### Citations

**File:** merkle-tools/src/lib.rs (L9-31)
```rust
    while current_hashes.len() > 1 {
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
        }

        if transaction_position % 2 == 1 {
            merkle_proof.push(current_hashes[transaction_position - 1].clone());
        } else {
            merkle_proof.push(current_hashes[transaction_position + 1].clone());
        }

        let mut new_hashes = Vec::new();

        for i in (0..current_hashes.len() - 1).step_by(2) {
            new_hashes.push(compute_hash(&current_hashes[i], &current_hashes[i + 1]));
        }

        current_hashes = new_hashes;
        transaction_position /= 2;
    }

    merkle_proof
}
```

**File:** merkle-tools/src/lib.rs (L33-52)
```rust
#[must_use]
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

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
