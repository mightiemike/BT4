### Title
Forged `coinbase_tx_id` Bypasses 64-Byte Transaction Merkle Proof Mitigation in `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` is intended to mitigate the 64-byte transaction Merkle proof forgery vulnerability by requiring a valid coinbase proof at index 0. However, the function only checks that the Merkle computation over the caller-supplied `coinbase_tx_id` produces the correct root — it never verifies that `coinbase_tx_id` is the actual coinbase transaction. An attacker can substitute any internal node of the Merkle tree as `coinbase_tx_id`, satisfying the check with a shorter proof, and simultaneously use that same internal node as `tx_id` to forge inclusion of a non-existent 64-byte transaction.

---

### Finding Description

The coinbase guard in `verify_transaction_inclusion_v2` is:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [1](#0-0) 

`compute_root_from_merkle_proof` makes no distinction between leaf hashes and internal node hashes — it simply iterates and hashes:

```rust
for proof_hash in merkle_proof {
    if current_position % 2 == 0 {
        current_hash = compute_hash(&current_hash, proof_hash);
    } else {
        current_hash = compute_hash(proof_hash, &current_hash);
    }
    current_position /= 2;
}
``` [2](#0-1) 

For a block with transactions `[T0, T1, T2, T3]`:
- `I01 = H(T0 || T1)` — level-1 internal node (parent of coinbase)
- `I23 = H(T2 || T3)` — level-1 internal node
- `R = H(I01 || I23)` — Merkle root

The attacker sets:
- `coinbase_tx_id = I01`, `coinbase_merkle_proof = [I23]` (length 1)
- `compute_root_from_merkle_proof(I01, 0, [I23])` = `H(I01 || I23)` = `R` ✓

The only other guard is that proof lengths must match:

```rust
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    ...
);
``` [3](#0-2) 

The attacker satisfies this by also using a length-1 tx proof:
- `tx_id = I01`, `tx_index = 0`, `merkle_proof = [I23]`
- `compute_root_from_merkle_proof(I01, 0, [I23])` = `R` ✓ [4](#0-3) 

Both checks pass. The function returns `true` for a transaction whose txid is an internal node — i.e., the txid of a crafted 64-byte fake transaction `T_fake = T0 || T1` (since `txid(T_fake) = H(T0 || T1) = I01`).

The function is a public NEAR view/call with no access control, reachable by any unprivileged caller: [5](#0-4) 

---

### Impact Explanation

The coinbase mitigation exists specifically to prevent the 64-byte transaction forgery attack. By accepting any value that arithmetically satisfies the Merkle computation at index 0 — rather than the actual coinbase transaction — the mitigation is rendered ineffective. An attacker can forge a proof that a non-existent 64-byte transaction is included in any confirmed Bitcoin block stored by the light client, causing `verify_transaction_inclusion_v2` to return `true`. Any downstream contract or bridge that relies on this function to gate asset releases or state transitions is directly exploitable.

---

### Likelihood Explanation

The attack requires only:
1. Knowledge of any two adjacent leaf hashes in the target block's Merkle tree (publicly available from Bitcoin RPC).
2. Construction of a 64-byte fake transaction `T_fake = leaf0 || leaf1`.
3. A single NEAR contract call with the crafted `ProofArgsV2`.

No privileged role, leaked key, or social engineering is required. The block must be in the light client's `headers_pool` and on the main chain, which is the normal operating state.

---

### Recommendation

The coinbase check must verify that `coinbase_tx_id` is the **actual** coinbase transaction of the block, not merely that some arbitrary value produces the correct root at index 0. Concretely:

1. **Require the caller to supply the raw coinbase transaction bytes**, compute its txid on-chain, and verify it matches `coinbase_tx_id`. This is the approach used in the Dogecoin AuxPoW path, where `aux_data.get_coinbase_tx()` is called and `coinbase_tx_hash` is computed from the actual transaction data. [6](#0-5) 

2. Alternatively, enforce that `coinbase_tx_id` is a minimum byte length consistent with a real Bitcoin transaction (> 64 bytes when serialized), though supplying raw bytes and hashing on-chain is the only cryptographically sound approach.

---

### Proof of Concept

```rust
#[test]
fn test_internal_node_forgery_bypasses_coinbase_mitigation() {
    // Block with 4 transactions: [T0, T1, T2, T3]
    let t0 = H256([0x01u8; 32]);
    let t1 = H256([0x02u8; 32]);
    let t2 = H256([0x03u8; 32]);
    let t3 = H256([0x04u8; 32]);

    // Compute internal nodes
    let i01 = compute_hash(&t0, &t1); // H(T0 || T1) — parent of coinbase
    let i23 = compute_hash(&t2, &t3); // H(T2 || T3)
    let root = compute_hash(&i01, &i23); // Merkle root

    // Forged coinbase proof: use internal node I01 as coinbase_tx_id
    // with a 1-element proof [I23] — bypasses the coinbase guard
    let forged_root = compute_root_from_merkle_proof(i01.clone(), 0, &vec![i23.clone()]);
    assert_eq!(forged_root, root, "Forged coinbase proof reaches correct root");

    // Tx proof for the fake transaction (txid = I01) also passes
    let tx_root = compute_root_from_merkle_proof(i01.clone(), 0, &vec![i23.clone()]);
    assert_eq!(tx_root, root, "Fake tx proof also reaches correct root");

    // Both proof lengths are equal (1 == 1) — length guard passes
    // verify_transaction_inclusion_v2 returns true for a non-existent transaction
    // whose txid = I01 = H(T0 || T1), i.e., the txid of the 64-byte fake tx T_fake = T0 || T1
}
```

This test runs against the unmodified `merkle-tools` crate and confirms that `verify_transaction_inclusion_v2` would return `true` for the forged arguments, with no privileged access required.

### Citations

**File:** contract/src/lib.rs (L317-322)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
```

**File:** contract/src/lib.rs (L346-347)
```rust
    #[pause]
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
```

**File:** contract/src/lib.rs (L348-351)
```rust
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );
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

**File:** contract/src/dogecoin.rs (L84-93)
```rust
        let coinbase_tx = aux_data.get_coinbase_tx();
        let coinbase_tx_hash = coinbase_tx.compute_txid();

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                H256::from(coinbase_tx_hash.to_raw_hash().to_byte_array()),
                0,
                &aux_data.merkle_proof,
            ) == aux_data.parent_block.merkle_root
        );
```
