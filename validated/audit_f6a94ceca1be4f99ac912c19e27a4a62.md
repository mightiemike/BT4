### Title
`verify_transaction_inclusion` Lacks Coinbase Merkle Proof Check Present in `verify_transaction_inclusion_v2`, Enabling 64-Byte Transaction Forgery - (File: `contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are both externally callable proof-verification entry points. The v2 function adds a mandatory coinbase Merkle proof check to prevent the 64-byte transaction Merkle proof forgery attack, but the original function omits this check entirely and remains callable by any unprivileged NEAR account. An attacker can call `verify_transaction_inclusion` directly with a crafted internal-node hash and a valid sibling path, causing the contract to return `true` for a transaction that does not exist.

### Finding Description

`verify_transaction_inclusion_v2` enforces two checks that `verify_transaction_inclusion` does not:

1. It requires `merkle_proof.len() == coinbase_merkle_proof.len()`.
2. It verifies that the coinbase transaction (position 0) is committed to the block's Merkle root before delegating to the inner proof check. [1](#0-0) 

`verify_transaction_inclusion` performs none of these checks. It only verifies that `merkle_proof` is non-empty and that the computed root matches `header.block_header.merkle_root`: [2](#0-1) 

The function is still decorated with `#[pause]` (callable when unpaused) and carries no `#[trusted_relayer]` or other caller restriction, so any NEAR account can invoke it: [3](#0-2) 

The code's own warning acknowledges the gap:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [4](#0-3) 

The inconsistency is structurally identical to the reference report: one function in a sibling pair performs a critical type/integrity check; the other does not, and both remain reachable.

### Impact Explanation

Any downstream NEAR contract or off-chain system that calls `verify_transaction_inclusion` to gate a cross-chain action (e.g., releasing funds, minting tokens, recording a settlement) can be deceived into accepting a fabricated transaction inclusion proof. The attacker does not need to mine any block; they only need to identify an existing mainchain block and construct a valid Merkle path from an internal 64-byte node to the known Merkle root. The function returns `true`, and the caller has no way to distinguish this from a genuine proof.

### Likelihood Explanation

The 64-byte transaction Merkle forgery technique is well-documented (referenced in the code itself via the BitMEX blog post). The attack requires only:
- Knowledge of any mainchain block's Merkle tree structure (public data).
- Construction of a 64-byte value whose double-SHA256 hash equals an internal Merkle node (feasible offline).
- A valid sibling path from that node to the Merkle root (derivable from the public block).

No privileged access, no mining, and no key material are required. The entry point is a public NEAR contract method.

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it so that it panics unconditionally (forcing all callers to migrate to `verify_transaction_inclusion_v2`). Deprecation alone is insufficient because the function remains callable. If backward compatibility must be preserved, add the same coinbase Merkle proof check inside `verify_transaction_inclusion` so both functions enforce the same invariant.

### Proof of Concept

1. Identify any mainchain block `B` stored in the contract (e.g., via `get_block_hash_by_height`).
2. Obtain `B`'s full transaction list and compute its Merkle tree offline.
3. Select an internal node `N` at depth `d` whose serialized left+right child concatenation is exactly 64 bytes (always true for SHA-256 digests).
4. Compute `tx_id = double_sha256(left_child || right_child)` — this equals `N`.
5. Build a sibling path `merkle_proof` of length `d` from `N` up to the Merkle root.
6. Call `verify_transaction_inclusion({ tx_id, tx_block_blockhash: B, tx_index: <position of N>, merkle_proof, confirmations: 1 })`.
7. The function computes `compute_root_from_merkle_proof(tx_id, index, proof)` which equals `B.merkle_root`, and returns `true` — falsely confirming inclusion of a non-existent transaction. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L287-323)
```rust
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

**File:** contract/src/lib.rs (L347-368)
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
