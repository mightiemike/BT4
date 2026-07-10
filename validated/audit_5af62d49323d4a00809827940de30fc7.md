Let me verify the exact proof arithmetic for the odd-leaf duplicate case.

## Proof Arithmetic Verification

Working through the math for a 3-transaction block [T0, T1, T2]:

**Bitcoin tree (odd → duplicate last leaf):**
```
Level 0: T0,  T1,  T2,  T2   (T2 duplicated)
Level 1: H(T0,T1),  H(T2,T2)
Level 2: H(H(T0,T1), H(T2,T2))  = root
```

**`compute_root_from_merkle_proof` with proof = [T2, H(T0,T1)]:**

| tx_index | Step 1 (pos%2) | Step 2 (pos/2=1, odd) | Result |
|---|---|---|---|
| 2 (even) | H(T2, T2) | H(H(T0,T1), H(T2,T2)) | = root ✓ |
| 3 (odd) | H(T2, T2) | H(H(T0,T1), H(T2,T2)) | = root ✓ |

The same proof `[T2, H(T0,T1)]` produces the correct root for **both** `tx_index=2` (real) and `tx_index=3` (phantom). The vulnerability is confirmed.

---

### Title
Merkle Proof Accepts Phantom Duplicate-Leaf Position in Odd-Width Tree — (`merkle-tools/src/lib.rs`)

### Summary

`compute_root_from_merkle_proof` performs no bounds check on `transaction_position` against the actual tree width. In any Bitcoin block whose transaction count is odd, the last leaf is duplicated. The resulting proof path for the real last leaf (index N-1) is arithmetically identical to the proof path for the phantom duplicate (index N), so both calls return the same root and `verify_transaction_inclusion` returns `true` for a position that does not exist in the block.

### Finding Description

`compute_root_from_merkle_proof` uses `transaction_position` only to decide left/right placement at each level via `current_position % 2`, then shifts right with `current_position /= 2`. [1](#0-0) 

No parameter carries the actual tree width, so the function cannot reject an out-of-range index. `verify_transaction_inclusion` passes the caller-supplied `args.tx_index` directly without any upper-bound check: [2](#0-1) 

`verify_transaction_inclusion_v2` adds a coinbase-proof guard but then delegates to the deprecated function unchanged, so it inherits the same flaw: [3](#0-2) 

For a block with N transactions where N is odd, Bitcoin duplicates leaf N-1 to form leaf N. The proof sibling at the first level for position N-1 is `tx[N-1]` itself. For position N (phantom), the sibling is also `tx[N-1]`. Both produce `H(tx[N-1], tx[N-1])` at level 1, and the rest of the path is identical. The function returns the same root for both indices.

### Impact Explanation

Any downstream bridge, unlock, mint, or withdrawal contract that calls `verify_transaction_inclusion` (or `_v2`) and uses `(block_hash, tx_index)` as its replay-prevention key can be double-spent: the attacker first redeems with the real index N-1, then replays with the phantom index N using the identical `tx_id` and proof. Both calls return `true` against the same canonical block header stored in the light client.

### Likelihood Explanation

- `verify_transaction_inclusion` is public, requires no role, and is callable by any account. [4](#0-3) 
- Odd transaction counts are the norm on Bitcoin (most blocks have an odd number of transactions).
- The attacker controls all five proof arguments (`tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`) and needs only a valid canonical block already stored in the light client.
- The only prerequisite is that the target block has an odd transaction count, which is trivially observable on-chain.

### Recommendation

Pass the total transaction count (tree leaf count) into `compute_root_from_merkle_proof` and assert `transaction_position < leaf_count` before the loop. Alternatively, derive the maximum valid index from `merkle_proof.len()` as `2^proof_len - 1` and reject any `tx_index` that exceeds the last real leaf. The `ProofArgs` struct should carry a `tx_count` field so the verifier can enforce this bound. [5](#0-4) 

### Proof of Concept

```
Block B: 3 transactions [T0, T1, T2]
Merkle tree (Bitcoin odd-leaf duplication):
  Level 0: T0, T1, T2, T2
  Level 1: H(T0,T1),  H(T2,T2)
  Root:    H(H(T0,T1), H(T2,T2))

Proof P = [T2, H(T0,T1)]

Call 1 (legitimate):
  verify_transaction_inclusion(tx_id=T2, tx_block_blockhash=B, tx_index=2,
                                merkle_proof=P, confirmations=1)
  → compute_root_from_merkle_proof(T2, 2, P):
      pos=2 (even): hash = H(T2, T2)
      pos=1 (odd):  hash = H(H(T0,T1), H(T2,T2)) == root  → true ✓

Call 2 (replay with phantom index):
  verify_transaction_inclusion(tx_id=T2, tx_block_blockhash=B, tx_index=3,
                                merkle_proof=P, confirmations=1)
  → compute_root_from_merkle_proof(T2, 3, P):
      pos=3 (odd):  hash = H(T2, T2)
      pos=1 (odd):  hash = H(H(T0,T1), H(T2,T2)) == root  → true ✓

Both calls return true. A bridge keyed on (block_hash, tx_index) processes T2 twice.
```

The same arithmetic applies to `verify_transaction_inclusion_v2`: the coinbase guard validates only that `coinbase_tx_id` at index 0 matches the root; it does not constrain the target `tx_index`. [6](#0-5)

### Citations

**File:** merkle-tools/src/lib.rs (L39-51)
```rust
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
```

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L318-322)
```rust
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
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
