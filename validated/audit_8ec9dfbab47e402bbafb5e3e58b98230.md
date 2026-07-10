### Title
Deprecated `verify_transaction_inclusion` Remains a Live, Callable NEAR Method Enabling Merkle Proof Forgery — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` is marked `#[deprecated]` but is still a fully live, publicly callable NEAR contract method. The Rust `#[deprecated]` attribute only emits a compiler warning; it does not gate or remove the on-chain entry point. Any unprivileged NEAR caller can invoke it directly, bypassing the coinbase-anchored Merkle proof check introduced in `verify_transaction_inclusion_v2`, and obtain a `true` return value for a forged transaction inclusion proof.

### Finding Description

The contract exposes two transaction inclusion verification methods:

- `verify_transaction_inclusion` (v1): verifies only that `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == header.merkle_root`. It does **not** validate that `tx_id` is a leaf-level transaction hash rather than an internal Merkle tree node.
- `verify_transaction_inclusion_v2` (v2): adds a coinbase proof requirement — it first verifies that `coinbase_tx_id` at index 0 produces the same `merkle_root`, anchoring the proof to a real transaction. This mitigates the 64-byte transaction Merkle forgery attack documented at https://www.bitmex.com/blog/64-Byte-Transactions.

The v1 function carries an explicit self-documenting warning:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [1](#0-0) 

Despite this, v1 is still decorated with `#[pause]` and `pub`, making it a live NEAR contract entry point. The `#[deprecated]` attribute has no runtime effect. [2](#0-1) 

The `compute_root_from_merkle_proof` function in `merkle-tools/src/lib.rs` performs no validation that the input hash is a leaf node — it simply walks the proof path and returns the computed root. [3](#0-2) 

### Impact Explanation

An attacker who knows the Merkle tree structure of any confirmed Bitcoin block can craft a `ProofArgs` where `tx_id` is an internal Merkle tree node hash (not a real transaction). Because `compute_root_from_merkle_proof` treats the input hash identically regardless of whether it is a leaf or an internal node, the computed root will match the block's `merkle_root`, and `verify_transaction_inclusion` returns `true`.

Any consumer contract that calls `verify_transaction_inclusion` — e.g., to gate a cross-chain asset release, atomic swap settlement, or state verification — can be deceived into accepting a proof for a transaction that does not exist. The corrupted value is the boolean proof result returned to the consumer, which is the sole authorization signal for downstream actions.

### Likelihood Explanation

The attack requires only knowledge of a real Bitcoin block's Merkle tree (publicly available) and the ability to call a NEAR contract method. No privileged role, private key, or social engineering is needed. The entry point is unconditionally reachable by any NEAR account. Consumer contracts that integrated before v2 was introduced, or that call v1 directly, are immediately exploitable.

### Recommendation

Remove `verify_transaction_inclusion` as a callable public NEAR method. The `#[deprecated]` attribute does not prevent on-chain invocation. The function should either be deleted or converted to a private helper. All callers must be migrated to `verify_transaction_inclusion_v2`. [4](#0-3) 

### Proof of Concept

1. Identify a confirmed Bitcoin block `B` with known Merkle tree. Let the tree have at least 4 transactions: `[tx0, tx1, tx2, tx3]`.
2. Compute `internal_node = double_sha256(tx2 || tx3)` — this is an internal node at depth 1, not a transaction.
3. Construct `ProofArgs`:
   - `tx_id` = `internal_node`
   - `tx_index` = 1 (position of `internal_node` among depth-1 nodes)
   - `merkle_proof` = `[double_sha256(tx0 || tx1)]` (the sibling at depth 1)
   - `tx_block_blockhash` = hash of block `B`
   - `confirmations` = 1
4. Call `verify_transaction_inclusion(args)` on the NEAR contract.
5. `compute_root_from_merkle_proof(internal_node, 1, [double_sha256(tx0||tx1)])` computes `double_sha256(double_sha256(tx0||tx1) || internal_node)` = the actual `merkle_root` of block `B`.
6. The function returns `true` for a `tx_id` that is not a transaction. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-279)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
```

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-323)
```rust
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
