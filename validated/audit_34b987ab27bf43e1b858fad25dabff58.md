### Title
Duplicate-Leaf Merkle Index Aliasing Allows False Inclusion Proof for Non-Existent Transaction Position — (`merkle-tools/src/lib.rs`, `contract/src/lib.rs`)

---

### Summary

`compute_root_from_merkle_proof` contains no bound check on `transaction_position` against the actual leaf count. For any Bitcoin block with an odd number of transactions N, the proof generated for the real last leaf at index N-1 also verifies correctly when submitted with index N (one past the end). Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are publicly callable by any NEAR account and pass the attacker-supplied `tx_index` directly into the proof recomputation with no upper-bound guard.

---

### Finding Description

Bitcoin's merkle tree construction duplicates the last leaf when the leaf count is odd. `merkle_proof_calculator` encodes this by appending a copy of the last hash before processing each odd-length level: [1](#0-0) 

For a 3-transaction block `[tx0, tx1, tx2]`, the padded level becomes `[tx0, tx1, tx2, tx2]`. The proof generated for position 2 is:

```
proof = [tx2, hash(tx0,tx1)]
```

because at position 2 (even), the sibling is `current_hashes[3]` = `tx2`.

`compute_root_from_merkle_proof` reconstructs the root by iterating over proof elements and branching on `current_position % 2`: [2](#0-1) 

**Legitimate call** — `compute_root_from_merkle_proof(tx2, 2, [tx2, hash(tx0,tx1)])`:
- Step 1: position=2 (even) → `hash(tx2, proof[0]=tx2)` = `hash(tx2,tx2)`, position→1
- Step 2: position=1 (odd) → `hash(proof[1]=hash(tx0,tx1), hash(tx2,tx2))` = root ✓

**Attack call** — `compute_root_from_merkle_proof(tx2, 3, [tx2, hash(tx0,tx1)])`:
- Step 1: position=3 (odd) → `hash(proof[0]=tx2, tx2)` = `hash(tx2,tx2)`, position→1
- Step 2: position=1 (odd) → `hash(proof[1]=hash(tx0,tx1), hash(tx2,tx2))` = root ✓

Because both operands at step 1 are the same hash `tx2`, swapping their order (even vs. odd branch) produces an identical result. The root computed for the ghost index N equals the root computed for the real index N-1, so the comparison in `verify_transaction_inclusion` passes: [3](#0-2) 

Neither function stores nor checks the actual transaction count of the block. `ProofArgs.tx_index` is a caller-supplied `u64` with no upper-bound validation: [4](#0-3) 

`verify_transaction_inclusion` is a plain `pub` method gated only by `#[pause]` — no `#[trusted_relayer]`, no `#[private]`: [5](#0-4) 

`verify_transaction_inclusion_v2` adds a coinbase proof check at hardcoded index `0usize`, but then delegates to `verify_transaction_inclusion` with the attacker's `tx_index` intact: [6](#0-5) 

The coinbase guard does not constrain the target transaction's index in any way, so v2 is equally vulnerable.

---

### Impact Explanation

Any NEAR account can call `verify_transaction_inclusion` (or v2) with:
- `tx_id` = real `tx[N-1]` hash from a canonical block with N odd transactions
- `tx_index` = N (the ghost position)
- `merkle_proof` = the legitimate proof for position N-1

The function returns `true` for a transaction position that does not exist in the block. Any downstream contract or bridge that trusts this return value to authorize a cross-chain settlement, token release, or asset unlock can be defrauded using a transaction that was already proven at its real position N-1, effectively enabling double-use of a single on-chain event or proof of a completely fabricated position.

---

### Likelihood Explanation

- No privileged role is required; the call is open to any NEAR account.
- The attacker only needs to know the real merkle proof for the last transaction in any odd-count block — this is public Bitcoin data derivable from any block explorer or full node.
- Every Bitcoin block with an odd transaction count (roughly half of all blocks) is a valid target.
- The math is deterministic and requires no brute force.

---

### Recommendation

1. **Store and validate `tx_index` bounds.** The block header does not contain a transaction count, so the contract must either (a) require callers to supply the total transaction count and validate `tx_index < tx_count`, or (b) require the proof length to equal `ceil(log2(tx_count))` and enforce that the reconstructed padded index does not exceed the padded tree size.

2. **Reject even-position proofs whose sibling equals the node itself.** At each level where `current_position` is even and `proof_hash == current_hash`, the proof is traversing a duplicated-leaf path. Treating this as invalid eliminates the aliasing for ghost indices.

3. **Enforce proof-length consistency with a supplied `tx_count`.** Require `merkle_proof.len() == ceil(log2(tx_count))` and `tx_index < tx_count`, rejecting any call where `tx_index` falls in the phantom range `[tx_count, next_power_of_two(tx_count))`.

---

### Proof of Concept

Concrete unit test targeting `merkle-tools/src/lib.rs`:

```rust
#[test]
fn test_ghost_index_aliasing() {
    // 3-tx block: odd count triggers duplicate-leaf padding
    let tx0 = double_sha256(b"tx0");
    let tx1 = double_sha256(b"tx1");
    let tx2 = double_sha256(b"tx2");
    let txs = vec![tx0.clone(), tx1.clone(), tx2.clone()];

    // Real proof for the last real leaf (position 2)
    let proof_for_2 = merkle_proof_calculator(txs.clone(), 2);

    // Legitimate verification at position 2 → must return real root
    let real_root = compute_root_from_merkle_proof(tx2.clone(), 2, &proof_for_2);

    // Attack: same tx_id, same proof, but ghost index 3 (N, one past the end)
    let ghost_root = compute_root_from_merkle_proof(tx2.clone(), 3, &proof_for_2);

    // Both return the same root — the invariant is broken
    assert_eq!(real_root, ghost_root,
        "ghost index 3 aliases real index 2: false inclusion proof accepted");
}
```

Wire into a NEAR contract test by calling `verify_transaction_inclusion` with `tx_index=3` against a canonical 3-transaction block; the function returns `true` despite position 3 not existing in the block.

### Citations

**File:** merkle-tools/src/lib.rs (L10-11)
```rust
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
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

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L317-323)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L358-368)
```rust
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

**File:** btc-types/src/contract_args.rs (L18-24)
```rust
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
```
