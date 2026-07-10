### Title
Deprecated `verify_transaction_inclusion` Remains a Live Callable Method, Silently Enabling 64-Byte Transaction Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` was deprecated in v0.5.0 to address the 64-byte transaction Merkle proof forgery vulnerability. Its critical security warning is expressed only as a Rust `#[deprecated]` compiler attribute and a `# Warning` doc-comment block — neither of which constitutes a runtime guard on NEAR. The function remains a fully callable public method. Any unprivileged NEAR caller or downstream contract that invokes it can receive a `true` proof result for a fabricated transaction, enabling irreversible fund release based on a forged Bitcoin SPV proof.

---

### Finding Description

`verify_transaction_inclusion` was superseded by `verify_transaction_inclusion_v2`, which adds a coinbase Merkle proof check that closes the forgery path. The old function was not removed; it is still decorated `#[pause]` and exposed as a public NEAR method. [1](#0-0) 

The entire security warning is:

1. A Rust `#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]` attribute — a compiler hint, invisible at NEAR runtime.
2. A `# Warning` block in the doc comment stating the function "may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [2](#0-1) 

Neither prevents the function from being called on-chain. The underlying `compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` performs no validation that the supplied `transaction_hash` is a leaf node (a real transaction) rather than an internal 64-byte Merkle node. It simply hashes the input up the tree and compares to the stored `merkle_root`. [3](#0-2) 

The final comparison in `verify_transaction_inclusion` is: [4](#0-3) 

There is no check that `args.tx_id` corresponds to a leaf-level transaction. An internal Merkle node presented as `tx_id` with a valid sibling path will satisfy this equality and return `true`.

---

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` and acts on a `true` result (e.g., releasing bridged assets, minting tokens, or unlocking collateral) can be deceived by a forged proof. The attacker does not need to mine a special block — they only need to identify an existing mainchain block whose Merkle tree contains a 64-byte internal node, then supply a valid Merkle path for that node. The contract returns `true`, and the downstream contract releases funds irreversibly. The corrupted value is the **proof result** (`bool`) returned to the caller.

---

### Likelihood Explanation

Moderate. The function is publicly callable by any NEAR account with no role restriction beyond the `#[pause]` gate. Integrators building on top of this light client who do not carefully read the Rust doc comment — or who compiled against an older ABI before the deprecation — will call the deprecated endpoint. The 64-byte transaction forgery attack is well-documented and tooling exists to construct such proofs against real Bitcoin blocks already accepted into the contract's mainchain.

---

### Recommendation

Remove `verify_transaction_inclusion` entirely from the contract's public interface, or add an unconditional `env::panic_str("use verify_transaction_inclusion_v2")` at the top of the function body to make it revert at runtime. A Rust `#[deprecated]` attribute is a compile-time hint only and provides zero on-chain enforcement. The critical warning is structurally buried — analogous to the external report's finding that a one-line buried warning is insufficient to prevent users from taking a harmful irreversible action.

---

### Proof of Concept

1. Identify any Bitcoin block already accepted into the contract's mainchain with ≥2 transactions (so internal Merkle nodes exist).
2. Select an internal Merkle node `N` (the 64-byte concatenation of two child hashes). Compute `tx_id = double_sha256(N)`.
3. Construct a valid Merkle proof path from `tx_id` up to the block's `merkle_root` using the sibling hashes at each level.
4. Call `verify_transaction_inclusion` with `tx_id`, the block's hash, the `tx_index` corresponding to the internal node's position, and the constructed `merkle_proof`.
5. `compute_root_from_merkle_proof(tx_id, tx_index, &merkle_proof)` equals `header.block_header.merkle_root` — the call returns `true`. [5](#0-4) 

6. A downstream contract gating fund release on this `true` result is deceived into releasing funds for a transaction that does not exist on the Bitcoin chain.

### Citations

**File:** contract/src/lib.rs (L276-288)
```rust
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
