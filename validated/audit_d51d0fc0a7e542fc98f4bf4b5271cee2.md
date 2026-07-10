### Title
Deprecated `verify_transaction_inclusion` Accepts Internal Merkle Tree Nodes as Valid Transaction IDs, Enabling Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated-but-still-callable `verify_transaction_inclusion` function performs no check that the caller-supplied `tx_id` is a leaf node in the Merkle tree. An unprivileged NEAR caller can supply an internal Merkle tree node hash as `tx_id`, causing the function to return `true` for a transaction that does not exist in the block. This is the direct analog of the signature-malleability class: a raw cryptographic primitive (Merkle proof computation) that accepts multiple structurally valid inputs for the same commitment, enabling proof forgery.

---

### Finding Description

`verify_transaction_inclusion` (lines 288–323 of `contract/src/lib.rs`) computes a Merkle root from the caller-supplied `tx_id` and `merkle_proof` and compares it against the stored block header's `merkle_root`:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

There is no check that `args.tx_id` is a leaf-level transaction hash rather than an internal Merkle tree node. Bitcoin's Merkle tree is built by double-SHA256-hashing 64-byte concatenations of 32-byte child hashes. A specially crafted 64-byte transaction produces a hash identical to an internal node. An attacker who knows a block's Merkle tree structure can:

1. Identify an internal node hash at depth D.
2. Construct a `merkle_proof` of length D−1 that walks from that internal node to the root.
3. Call `verify_transaction_inclusion` with `tx_id = internal_node_hash` and the crafted proof.
4. The function returns `true`, falsely confirming inclusion of a non-existent transaction.

The contract's own deprecation notice acknowledges this broken invariant:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [2](#0-1) 

`verify_transaction_inclusion_v2` was introduced specifically to close this gap by requiring a coinbase Merkle proof of the same length, ensuring the proof depth is consistent with a leaf-level transaction:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [3](#0-2) 

The v1 function lacks this protection and remains a live public entry point.

---

### Impact Explanation

Any NEAR contract or off-chain application that calls `verify_transaction_inclusion` to authorize actions (e.g., releasing bridged assets, confirming cross-chain payments) can be deceived into accepting a forged proof of transaction inclusion. The attacker does not need to mine a block, control any privileged role, or possess any private key. All required data (block Merkle tree structure) is publicly available from the Bitcoin network. The corrupted state is the proof result returned to the caller: `true` for a transaction that does not exist in the block.

---

### Likelihood Explanation

Medium. The function is deprecated and carries a documented warning, but it remains a live, unpermissioned public entry point protected only by the `#[pause]` attribute (which is not activated by default). Legacy integrations or callers unaware of the deprecation remain exposed. The 64-byte Merkle forgery technique is well-documented (referenced in the contract's own deprecation notice at the bitmex.com link) and requires only public on-chain data to execute. [4](#0-3) 

---

### Recommendation

Remove `verify_transaction_inclusion` entirely, or replace its body with an unconditional `env::panic_str("use verify_transaction_inclusion_v2")` to force all callers to migrate. The `#[deprecated]` attribute in Rust is a compile-time lint only and does not prevent on-chain invocation. As long as the function body exists and is reachable, any NEAR account can call it.

---

### Proof of Concept

1. Select any confirmed Bitcoin block with ≥2 transactions stored in the contract's `headers_pool`.
2. Obtain the block's full transaction list from a Bitcoin node and reconstruct its Merkle tree.
3. Identify an internal node hash `N` at depth 1 (i.e., `double_sha256(tx0 || tx1)`).
4. Construct `merkle_proof` = `[sibling_of_N_at_depth_1, parent_at_depth_2, ...]` — a path of length `tree_depth − 1` from `N` to the root.
5. Call `verify_transaction_inclusion` with:
   - `tx_id` = `N` (the internal node hash, not a real transaction)
   - `tx_block_blockhash` = the block hash
   - `tx_index` = the position of `N` in its level of the tree
   - `merkle_proof` = the crafted path
   - `confirmations` = 1
6. `compute_root_from_merkle_proof` correctly reconstructs the Merkle root from `N` and the proof, the comparison passes, and the function returns `true` — despite no such transaction existing in the block. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L263-288)
```rust
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
```

**File:** contract/src/lib.rs (L318-322)
```rust
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
```

**File:** contract/src/lib.rs (L358-365)
```rust
        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );
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
