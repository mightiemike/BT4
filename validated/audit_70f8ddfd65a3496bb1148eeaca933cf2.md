### Title
Deprecated `verify_transaction_inclusion` Accepts Internal Merkle Tree Nodes as Valid Transaction IDs Without Cross-Validation — (`contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function accepts any 32-byte hash as `tx_id` and returns `true` if the supplied Merkle proof computes to the block's stored Merkle root — without cross-validating that `tx_id` is a real leaf-level transaction rather than an internal Merkle tree node. This is the direct analog of H-09: just as the price oracle uses Chainlink's return value without cross-checking it against Uniswap, this function uses the Merkle proof result without cross-checking it against a coinbase proof. The function remains publicly callable on-chain despite its `#[deprecated]` annotation.

---

### Finding Description

`verify_transaction_inclusion` in `contract/src/lib.rs` performs the following check:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

It computes a Merkle root from the caller-supplied `tx_id` and `merkle_proof`, then compares it to the block's stored `merkle_root`. If they match, it returns `true`. There is no check that `tx_id` is a leaf node (a real transaction hash) rather than an internal node of the Merkle tree.

In Bitcoin's Merkle tree, every internal node is itself a 32-byte hash produced by hashing two child hashes. An attacker who knows the structure of a real block's Merkle tree can:

1. Pick any internal node `N` at depth `D` in the tree.
2. Construct a valid Merkle proof of length `D` (the sibling path from depth `D` to the root).
3. Call `verify_transaction_inclusion` with `tx_id = N`, `tx_index = position_of_N`, and `merkle_proof = sibling_path`.
4. `compute_root_from_merkle_proof` correctly reconstructs the Merkle root from `N` and the sibling path, the comparison succeeds, and the function returns `true`.

The function's own documentation acknowledges this:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [2](#0-1) 

`verify_transaction_inclusion_v2` mitigates this by requiring a coinbase Merkle proof of the **same length** as the target transaction proof:

```rust
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    ...
);
``` [3](#0-2) 

Because the coinbase transaction is always a leaf at the maximum depth of the tree, any internal-node proof (which is shorter) will fail the length equality check. The deprecated v1 function has no such cross-validation.

Despite being marked `#[deprecated]`, the function is still a public `#[pause]`-gated NEAR method. Rust's `#[deprecated]` attribute emits only a compiler warning; it does not remove the method from the contract's ABI or prevent on-chain invocation. [4](#0-3) 

---

### Impact Explanation

Any downstream NEAR contract or user that calls `verify_transaction_inclusion` to authorize an action (e.g., releasing bridged BTC, minting wrapped tokens, settling a payment) can be deceived into processing a non-existent Bitcoin transaction. The attacker does not need to mine any blocks or control any privileged role — they only need to know the Merkle tree structure of a real block already accepted by the light client.

---

### Likelihood Explanation

The Merkle tree structure of every Bitcoin block is public. An attacker can compute all internal node hashes offline, select any internal node, and construct the corresponding sibling proof. The call to `verify_transaction_inclusion` is permissionless (any NEAR account can invoke it). The attack is fully deterministic and requires no mining or key compromise.

---

### Recommendation

Remove `verify_transaction_inclusion` from the contract entirely. It is already superseded by `verify_transaction_inclusion_v2`. Keeping a deprecated but callable function with a documented forgery vector is unnecessary risk. If removal is not immediately possible, add the same coinbase proof cross-validation that `verify_transaction_inclusion_v2` performs. [5](#0-4) 

---

### Proof of Concept

Given a real Bitcoin block at height `H` already stored in the light client's mainchain:

1. Fetch the block's full transaction list and compute its Merkle tree offline.
2. Select any internal node `N` at depth `D` (e.g., the root's left child at depth 1).
3. Collect the sibling path of length `D` from `N` to the root.
4. Call on-chain:
   ```
   verify_transaction_inclusion({
     tx_id: N,                        // internal node hash, not a real txid
     tx_block_blockhash: block_hash,  // real block in mainchain
     tx_index: left_child_index,      // position of N among nodes at depth D
     merkle_proof: sibling_path,      // D siblings, not a full leaf-to-root path
     confirmations: 1
   })
   ```
5. `compute_root_from_merkle_proof(N, index, sibling_path)` reconstructs the correct Merkle root; the function returns `true`, falsely attesting that a transaction with hash `N` was included in block `H`. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L277-280)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
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
