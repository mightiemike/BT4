### Title
Missing `tx_index < 2^len(proof)` Bound Check Allows Wrong-Index Inclusion Acceptance — (`merkle-tools/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` uses only the lower `L` bits of `transaction_position` (where `L = len(merkle_proof)`) to traverse the tree. Any `tx_index K` where `K ≡ k (mod 2^L)` produces an identical computation to the valid index `k`. Because neither `compute_root_from_merkle_proof` nor `verify_transaction_inclusion` checks that `tx_index < 2^len(proof)`, an unprivileged caller can submit a valid proof with `tx_index = k + n·2^L` (n ≥ 1) and receive `true`, even though that leaf position does not exist in the block.

---

### Finding Description

In `merkle-tools/src/lib.rs`, `compute_root_from_merkle_proof` iterates exactly `L = merkle_proof.len()` times:

```rust
for proof_hash in merkle_proof {
    if current_position % 2 == 0 {
        current_hash = compute_hash(&current_hash, proof_hash);
    } else {
        current_hash = compute_hash(proof_hash, &current_hash);
    }
    current_position /= 2;
}
``` [1](#0-0) 

Each iteration consumes exactly one bit of `current_position` via `% 2` and `/ 2`. After `L` iterations the loop ends regardless of the remaining value of `current_position`. This means the traversal path is determined solely by the **lower L bits** of `transaction_position`. For any valid triple `(T, k, proof)` with `len(proof) = L`:

```
compute_root_from_merkle_proof(T, k + n·2^L, proof)
  == compute_root_from_merkle_proof(T, k, proof)   ∀ n ≥ 1
```

`verify_transaction_inclusion` passes `args.tx_index` directly to this function with no upper-bound check:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

The only guard present is `require!(!args.merkle_proof.is_empty(), ...)`. [3](#0-2) 

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its coinbase check, so it inherits the same flaw. [4](#0-3) 

---

### Impact Explanation

An attacker who observes any valid on-chain proof `(T, k, proof_of_length_L)` can call `verify_transaction_inclusion` with `tx_index = k + 2^L`. The function returns `true` for a leaf position that does not exist in the block (a block with a depth-L tree has at most `2^L` leaves, so valid indices are `0 … 2^L − 1`). Any downstream protocol that trusts the `(tx_id, tx_block_blockhash, tx_index)` triple as a unique identifier — e.g., to gate a one-time payment release, UTXO unlock, or cross-chain bridge action — can be triggered a second (or Nth) time with the same transaction but a different, fabricated index, bypassing replay-protection logic that keys on `tx_index`.

---

### Likelihood Explanation

The attack requires no privileged role, no key compromise, and no special chain state. Any caller who can observe a previously submitted valid proof (all NEAR state is public) can immediately construct the exploit input. The call path is fully public (`#[pause]` is the only gate, and it defaults to unpaused).

---

### Recommendation

Add a bounds check in `compute_root_from_merkle_proof` (or at the call site in `verify_transaction_inclusion`) before the loop:

```rust
assert!(
    transaction_position < (1usize << merkle_proof.len()),
    "tx_index out of range for proof length"
);
```

This ensures the supplied index is a valid leaf position in a tree of the claimed depth, closing the aliasing gap.

---

### Proof of Concept

Given a real Bitcoin block with 8 transactions and a valid proof of length 3 for tx at index `k = 0`:

```rust
let valid_proof = merkle_proof_calculator(tx_hashes.clone(), 0); // len = 3
let root = compute_root_from_merkle_proof(tx_hashes[0].clone(), 0, &valid_proof);

// Attacker submits tx_index = 0 + 2^3 = 8 (does not exist in an 8-tx block)
let forged = compute_root_from_merkle_proof(tx_hashes[0].clone(), 8, &valid_proof);

assert_eq!(root, forged); // passes — same hash, wrong index accepted
```

Calling `verify_transaction_inclusion` with `tx_index = 8` on a block whose tree has depth 3 (8 leaves, indices 0–7) returns `true`, accepting a leaf position that does not exist.

### Citations

**File:** merkle-tools/src/lib.rs (L42-49)
```rust
    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }
```

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

**File:** contract/src/lib.rs (L318-322)
```rust
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
```

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```
