### Title
Odd-Width Merkle Tree Phantom Position Acceptance — (`merkle-tools/src/lib.rs::compute_root_from_merkle_proof`, `contract/src/lib.rs::verify_transaction_inclusion`)

---

### Summary

`compute_root_from_merkle_proof` never validates that `transaction_position` is within the actual tree bounds. In any block with an odd number of transactions, Bitcoin's Merkle construction duplicates the last leaf. This means the same proof branch that validates the real last transaction at position `N-1` also validates the same `tx_id` at the phantom position `N` (which does not exist in the block). Both `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` return `true` for this phantom position.

---

### Finding Description

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` uses only the parity of `current_position` at each level to decide left/right hashing: [1](#0-0) 

There is no check that `transaction_position < tree_size`. The function accepts any caller-supplied position value.

Bitcoin's Merkle tree duplicates the last leaf when the count is odd: [2](#0-1) 

**Concrete example — 3-transaction block `[T0, T1, T2]`:**

```
Level 0 (padded): [T0, T1, T2, T2]
Level 1:          [H(T0,T1),  H(T2,T2)]
Root:              H(H(T0,T1), H(T2,T2))
```

Proof for `T2` at real position `2`: `[T2, H(T0,T1)]`

Verify with **phantom position `3`** using the same proof:
```
Step 1: pos=3 (odd)  → H(T2,   T2)        = H(T2,T2);   pos→1
Step 2: pos=1 (odd)  → H(H(T0,T1), H(T2,T2))            = Root ✓
```

`compute_root_from_merkle_proof(T2, 3, [T2, H(T0,T1)])` returns the correct root, so `verify_transaction_inclusion` returns `true` for `tx_index=3` — a position that does not exist in the block.

`verify_transaction_inclusion_v2` does not fix this. Its coinbase guard only checks that the coinbase proof is valid at position `0`; it does not constrain `tx_index` to be within bounds: [3](#0-2) 

After the coinbase check passes, it delegates directly to the deprecated `verify_transaction_inclusion`, which performs the same unconstrained position computation: [4](#0-3) 

---

### Impact Explanation

The contract certifies that `tx_id` exists at a position that is not present in the real block. Any downstream bridge, unlock, mint, or withdrawal contract that uses `(tx_id, block_hash, tx_index)` as its replay-prevention key will treat the phantom-position proof as a distinct, previously-unseen event. The attacker can submit the real proof `(T2, block_hash, 2, proof)` to trigger the first action, then submit `(T2, block_hash, 3, same_proof)` to trigger a second action for the same on-chain transaction — a cross-chain double-spend.

---

### Likelihood Explanation

- Requires only a block with an odd transaction count, which is common on Bitcoin mainnet.
- No privileged role, relayer compromise, or social engineering is needed.
- The attacker supplies all parameters (`tx_id`, `tx_index`, `merkle_proof`) directly to the public contract method.
- The proof is mechanically derivable from any Bitcoin block explorer.

---

### Recommendation

In `compute_root_from_merkle_proof`, reject any `transaction_position` that is out of range for the given proof depth. The maximum valid position for a proof of depth `d` is `2^d - 1`, but the tighter fix is to require the caller to also supply the actual transaction count and validate `transaction_position < tx_count` before entering the loop. Alternatively, the contract layer in `verify_transaction_inclusion` should accept and validate a `tx_count` parameter and assert `tx_index < tx_count`.

---

### Proof of Concept

```rust
// 3-tx block: [T0, T1, T2]
// Real Merkle root = H(H(T0,T1), H(T2,T2))
// Real proof for T2 at index 2: [T2, H(T0,T1)]

// Call 1 — real position, succeeds (expected):
verify_transaction_inclusion({ tx_id: T2, tx_index: 2, merkle_proof: [T2, H(T0,T1)], ... })
// → true  ✓

// Call 2 — phantom position, ALSO succeeds (bug):
verify_transaction_inclusion({ tx_id: T2, tx_index: 3, merkle_proof: [T2, H(T0,T1)], ... })
// → true  ✗  (position 3 does not exist in the block)
```

Step-by-step for call 2 inside `compute_root_from_merkle_proof`:
- `current_hash=T2, current_position=3`
- Iteration 1: `3 % 2 == 1` → `current_hash = H(T2, T2)`; `current_position = 1`
- Iteration 2: `1 % 2 == 1` → `current_hash = H(H(T0,T1), H(T2,T2))` = Root; `current_position = 0`
- Returns Root → matches `header.block_header.merkle_root` → function returns `true` [5](#0-4)

### Citations

**File:** merkle-tools/src/lib.rs (L9-11)
```rust
    while current_hashes.len() > 1 {
        if current_hashes.len() % 2 == 1 {
            current_hashes.push(current_hashes[current_hashes.len() - 1].clone());
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

**File:** contract/src/lib.rs (L318-322)
```rust
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
```

**File:** contract/src/lib.rs (L347-368)
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
```
