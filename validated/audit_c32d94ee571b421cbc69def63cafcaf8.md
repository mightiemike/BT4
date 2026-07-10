### Title
Wrong-Index Merkle Proof Accepted for Non-Existent Position via Odd-Length Leaf Duplication — (`merkle-tools/src/lib.rs`)

---

### Summary

The odd-length leaf-duplication in `merkle_proof_calculator` causes the last transaction's hash to appear as its own sibling in the proof. This creates a mathematical symmetry that allows `compute_root_from_merkle_proof` to accept the same `(tx_id, proof)` pair at **two different indices** — the real index `N` and the phantom index `N+1` — both producing the correct Merkle root. `verify_transaction_inclusion` has no guard against this, so it returns `true` for a claimed position that does not exist in the block.

---

### Finding Description

**Root cause — `merkle_proof_calculator`:**

When `current_hashes.len() % 2 == 1`, the last element is duplicated: [1](#0-0) 

For a 5-tx block at `transaction_position = 4` (even), the sibling selected is: [2](#0-1) 

`current_hashes[4 + 1]` is the just-appended duplicate of `h4`, so `proof[0] = h4` — the transaction's own hash.

**Trace for a 5-element tree `[h0, h1, h2, h3, h4]` at index 4:**

| Level | `current_hashes` after duplication | `transaction_position` | Sibling pushed to proof |
|---|---|---|---|
| 0 | `[h0,h1,h2,h3,h4,h4]` | 4 (even) | `h4` |
| 1 | `[H(h0,h1), H(h2,h3), H(h4,h4), H(h4,h4)]` | 2 (even) | `H(h4,h4)` |
| 2 | `[H(H(h0,h1),H(h2,h3)), H(H(h4,h4),H(h4,h4))]` | 1 (odd) | `H(H(h0,h1),H(h2,h3))` |

Proof = `[h4, H(h4,h4), H(H(h0,h1),H(h2,h3))]`

**Why index 5 also verifies — `compute_root_from_merkle_proof`:** [3](#0-2) 

Trace `compute_root_from_merkle_proof(h4, 5, proof)`:

| Step | `current_position` | parity | operation | result |
|---|---|---|---|---|
| 1 | 5 | odd | `H(proof[0], h4)` = `H(h4, h4)` | `H(h4,h4)` |
| 2 | 2 | even | `H(H(h4,h4), proof[1])` = `H(H(h4,h4), H(h4,h4))` | `H(H(h4,h4),H(h4,h4))` |
| 3 | 1 | odd | `H(proof[2], ...)` = `H(H(H(h0,h1),H(h2,h3)), H(H(h4,h4),H(h4,h4)))` | **root** |

This is identical to the result for index 4. Both return the correct Merkle root.

**`verify_transaction_inclusion` has no guard against this:** [4](#0-3) 

The only check is `computed_root == header.block_header.merkle_root`. There is no validation that `tx_index` is within the actual transaction count of the block, and no check that `proof[i] != tx_id`.

**`verify_transaction_inclusion_v2` is also affected:** [5](#0-4) 

v2 adds a coinbase proof check but then delegates to v1 unchanged, so the same wrong-index path is reachable through the non-deprecated entry point.

---

### Impact Explanation

`verify_transaction_inclusion` returns `true` for `(tx_id = h4, tx_index = 5)` in a 5-transaction block. Index 5 does not exist. This is a **wrong-index inclusion claim** accepted as valid.

**Clarification on the question's framing:** The `tx_id` used (`h4`) is a real transaction in the block at index 4 — it is not a fabricated transaction absent from the block. The question's claim of "a transaction not in the block" is therefore imprecise. What actually occurs is that the same real transaction is provable at a phantom index that does not exist in the block. This still satisfies the scoped impact criterion of "wrong index."

Downstream contracts that use `(tx_id, tx_index)` as a composite key to gate processing (e.g., to prevent double-redemption of a UTXO-backed claim) can be made to accept the same transaction twice — once at its real index and once at the phantom index — because both calls return `true`.

---

### Likelihood Explanation

- Any unprivileged NEAR account can call `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` directly; neither is `#[private]` or restricted to a trusted relayer role. [6](#0-5) 
- The precondition (a Bitcoin block with an odd transaction count) is satisfied by the majority of real Bitcoin blocks.
- The attacker only needs to know the last transaction's hash and the block hash, both of which are public on-chain data.
- No key material, privileged role, or social engineering is required.

---

### Recommendation

1. **Reject `tx_index >= block_tx_count`**: Store the transaction count in the block header metadata and require `tx_index < tx_count` before evaluating the proof.
2. **Reject self-sibling proofs**: In `compute_root_from_merkle_proof`, assert that no element of `merkle_proof` equals `transaction_hash` at the same level.
3. **Alternatively, fix `merkle_proof_calculator`**: When the last element is duplicated, record that the position is the last element and emit a sentinel or skip the self-sibling rather than pushing the duplicate into the proof path.

---

### Proof of Concept

```rust
// Pseudocode — directly runnable in merkle-tools/src/lib.rs test module

let tx_hashes = vec![h0, h1, h2, h3, h4]; // 5 transactions
let root = merkle_root_calculator(&tx_hashes);

// Legitimate proof for index 4
let proof = merkle_proof_calculator(tx_hashes.clone(), 4);

// Observation: proof[0] == h4 (self-sibling due to odd duplication)
assert_eq!(proof[0], h4);

// Legitimate verification at index 4 — expected to pass
assert_eq!(compute_root_from_merkle_proof(h4.clone(), 4, &proof), root);

// Wrong-index verification at index 5 — ALSO passes, index 5 does not exist
assert_eq!(compute_root_from_merkle_proof(h4.clone(), 5, &proof), root);

// Therefore verify_transaction_inclusion(h4, tx_index=5, block_hash, proof, confirmations)
// returns true for a non-existent position in the block.
```

The existing test `test_merkle_proof_verification_odd` in `merkle-tools/src/lib.rs` already constructs exactly this 5-element tree and proof for index 4 [7](#0-6)  — adding a second `assert_eq!` call with `transaction_position = 5` to that test would confirm the wrong-index acceptance without any modification to production code.

### Citations

**File:** merkle-tools/src/lib.rs (L10-11)
```rust
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
```

**File:** merkle-tools/src/lib.rs (L16-17)
```rust
        } else {
            merkle_proof.push(current_hashes[transaction_position + 1].clone());
```

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

**File:** merkle-tools/src/lib.rs (L154-173)
```rust
    #[test]
    fn test_merkle_proof_verification_odd() {
        let tx_hashes = vec![
            decode_hex("18afbf37d136ff62644b231fcde72f1fb8edd04a798fb00cb06360da635da275"),
            decode_hex("30b19832a5f4b952e151de77d96139987492becc8b6e1e914c4103cfbb06c01e"),
            decode_hex("b94ed12902e35b29dd53cf25e665b4d0bc92f22adbc383ad90566584902b061d"),
            decode_hex("1920e5d8a10018dc65308bb4d1f11d30b5406c6499688443bfcd1ef364206b14"),
            decode_hex("048f3897c16bdc59ec1187aa080a4b4aa5ec1afcb4b776cf8b8a214b01990a7b"),
        ];

        let calculated_merkle_root = merkle_root_calculator(&tx_hashes);
        let calculated_merkle_proof = merkle_proof_calculator(tx_hashes, 4);

        let computed_root_from_merkle_proof = compute_root_from_merkle_proof(
            decode_hex("048f3897c16bdc59ec1187aa080a4b4aa5ec1afcb4b776cf8b8a214b01990a7b"),
            4,
            &calculated_merkle_proof,
        );
        assert_eq!(computed_root_from_merkle_proof, calculated_merkle_root);
    }
```

**File:** contract/src/lib.rs (L287-288)
```rust
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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
```
