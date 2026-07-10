### Title
Duplicate-Leaf Path Collision Allows False Inclusion Proof at Nonexistent `tx_index` — (`merkle-tools/src/lib.rs`)

---

### Summary

`merkle_proof_calculator` duplicates the last leaf when the tree has an odd number of transactions. `compute_root_from_merkle_proof` is unaware of tree size and applies no duplication. For the last leaf of any odd-length tree, the proof element at depth 0 equals the transaction hash itself. This makes the first hash step commutative: the verifier produces the same intermediate hash whether `tx_index` is `last` (even) or `last+1` (odd). Any NEAR caller can submit the real proof for `tx_index = N` with `tx_index = N+1` and receive `true` from both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2`.

---

### Finding Description

**`merkle_proof_calculator` — odd-tree duplication:** [1](#0-0) 

When `current_hashes.len() % 2 == 1`, the last element is pushed again. For a 5-tx tree at `transaction_position = 4` (even), the sibling pushed into the proof at depth 0 is `current_hashes[4+1]` — which is the just-appended duplicate of `tx4` itself: [2](#0-1) 

So `proof[0] == tx4 == transaction_hash`.

**`compute_root_from_merkle_proof` — no tree-size awareness:** [3](#0-2) 

At depth 0, the hash step is:
- `tx_index = 4` (even): `H(current=tx4, proof[0]=tx4)` → `H(tx4, tx4)`
- `tx_index = 5` (odd): `H(proof[0]=tx4, current=tx4)` → `H(tx4, tx4)`

Both produce the identical intermediate value. All subsequent levels are identical, so both calls return `merkle_root`.

**`verify_transaction_inclusion` — no bounds check on `tx_index`:** [4](#0-3) 

The function accepts any caller-supplied `tx_index` with no upper-bound validation against the actual number of transactions in the block.

**`verify_transaction_inclusion_v2` — coinbase guard does not close the gap:** [5](#0-4) 

The coinbase check verifies a separate proof at index 0. It is independent of the `tx_index` supplied for the target transaction and does not prevent the collision at `tx_index = N+1`.

---

### Impact Explanation

The contract returns `true` for `(tx_id=tx4, block=B, tx_index=5)` even though index 5 does not exist in a 5-transaction block. The invariant that a Merkle proof uniquely binds a `tx_id` to a single `tx_index` is broken. Any downstream protocol that uses `(block_hash, tx_index)` as a unique key to prevent double-claiming — e.g., a bridge that records "this output at position N has been spent" — can be exploited: an attacker submits the same real transaction twice, once at index 4 and once at index 5, obtaining two successful verifications for one on-chain event.

---

### Likelihood Explanation

The call is open to any NEAR account with no access-control gate, no `#[private]` restriction, and no trusted-relayer requirement. The attacker needs only a real block with an odd transaction count (extremely common on Bitcoin/Litecoin/Dogecoin), the real txid of the last transaction, and the standard Merkle proof for that transaction. No privileged key, no social engineering, and no special chain state is required.

---

### Recommendation

Pass the total transaction count into `compute_root_from_merkle_proof` and reject any `tx_index >= tx_count`. Alternatively, validate inside `verify_transaction_inclusion` that `tx_index < tx_count` using the transaction count committed in the block (or derived from proof depth). At minimum, add a check that `proof[0] != transaction_hash` to reject the degenerate duplicate-leaf case.

---

### Proof of Concept

Concrete Rust unit test (can be added to `merkle-tools/src/lib.rs` tests, no external dependencies):

```rust
#[test]
fn test_odd_tree_index_collision() {
    // 5-tx tree: indices 0..4 exist; index 5 does not.
    let tx = |n: u8| H256([n; 32]);
    let txs = vec![tx(0), tx(1), tx(2), tx(3), tx(4)];

    // Generate legitimate proof for the last leaf (index 4).
    let proof = merkle_proof_calculator(txs.clone(), 4);

    // Verify at the real index — must pass.
    let root_at_4 = compute_root_from_merkle_proof(tx(4), 4, &proof);

    // Verify at the nonexistent index 5 — must NOT pass, but does.
    let root_at_5 = compute_root_from_merkle_proof(tx(4), 5, &proof);

    // Both return the same merkle root → false inclusion at index 5.
    assert_eq!(root_at_4, root_at_5,
        "VULNERABILITY: index 5 (nonexistent) produces the same root as index 4");
}
```

The collision holds because `proof[0] == tx4` (the duplicate leaf), making `H(tx4, proof[0])` and `H(proof[0], tx4)` identical. Submitting this to `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` on a live contract with a real odd-length block returns `true` for the nonexistent index. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** merkle-tools/src/lib.rs (L4-31)
```rust
pub fn merkle_proof_calculator(tx_hashes: Vec<H256>, transaction_position: usize) -> Vec<H256> {
    let mut transaction_position = transaction_position;
    let mut merkle_proof = Vec::new();
    let mut current_hashes = tx_hashes;

    while current_hashes.len() > 1 {
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
        }

        if transaction_position % 2 == 1 {
            merkle_proof.push(current_hashes[transaction_position - 1].clone());
        } else {
            merkle_proof.push(current_hashes[transaction_position + 1].clone());
        }

        let mut new_hashes = Vec::new();

        for i in (0..current_hashes.len() - 1).step_by(2) {
            new_hashes.push(compute_hash(&current_hashes[i], &current_hashes[i + 1]));
        }

        current_hashes = new_hashes;
        transaction_position /= 2;
    }

    merkle_proof
}
```

**File:** merkle-tools/src/lib.rs (L33-52)
```rust
#[must_use]
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

**File:** contract/src/lib.rs (L288-323)
```rust
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
