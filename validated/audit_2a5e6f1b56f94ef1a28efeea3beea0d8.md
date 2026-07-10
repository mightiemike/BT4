### Title
Odd-leaf duplicate position reuse allows `verify_transaction_inclusion_v2` to return `true` for a nonexistent `tx_index` — (`merkle-tools/src/lib.rs::compute_root_from_merkle_proof`)

---

### Summary

`compute_root_from_merkle_proof` uses only `current_position % 2` to decide left/right at each tree level. In a Bitcoin Merkle tree with an odd number of transactions, the last leaf is duplicated. This makes the proof for the real last leaf (index `N`) mathematically identical to a proof for the virtual duplicate position (index `N+1`). `verify_transaction_inclusion_v2` therefore returns `true` for `tx_index = N+1`, a position that does not correspond to any real transaction in the block.

---

### Finding Description

**Root cause — `compute_root_from_merkle_proof`** [1](#0-0) 

The verifier tracks position only to choose left/right (`% 2`) and then halves it (`/= 2`). It never validates that the supplied `transaction_position` is within the actual leaf count of the tree.

**Concrete arithmetic for a 3-transaction block `[T0, T1, T2]`**

Bitcoin's construction duplicates the last leaf when the count is odd:

```
Leaves:  [T0,  T1,  T2,  T2]   (T2 duplicated at index 3)
Level 1: [H(T0,T1),  H(T2,T2)]
Root:     H(H(T0,T1), H(T2,T2))
```

The canonical proof for T2 at index 2 is `proof = [T2, H(T0,T1)]`.

| Step | index 2 (real) | index 3 (virtual) |
|------|---------------|-------------------|
| pos=2 (even) | `hash(T2, T2)` → `H22`, pos→1 | pos=3 (odd) → `hash(T2, T2)` → `H22`, pos→1 |
| pos=1 (odd) | `hash(H(T0,T1), H22)` → Root ✓ | `hash(H(T0,T1), H22)` → Root ✓ |

Both paths produce the same root. The verifier accepts both.

**Entry point — `verify_transaction_inclusion_v2`** [2](#0-1) 

The coinbase guard (lines 358–365) only verifies that a separate coinbase proof is valid at index 0. It does not constrain the `tx_index` of the main transaction proof in any way. An attacker supplies the real coinbase proof unchanged and simply changes `tx_index` from `N` to `N+1` in the main proof arguments.

---

### Impact Explanation

`verify_transaction_inclusion_v2` returns `true` for a `tx_index` that does not correspond to any real transaction in the block. Any downstream bridge, unlock, mint, or withdrawal contract that:

- uses `(tx_id, block_hash, tx_index)` as a replay/nonce key, or
- uses `tx_index` to select which output or UTXO to process,

can be tricked into processing the same real transaction a second time under a different claimed index, or into processing an output at a position that was never broadcast.

---

### Likelihood Explanation

- Odd transaction counts are the norm in Bitcoin blocks (roughly half of all blocks).
- No cryptographic forgery is required; the attacker reuses a proof that any block explorer provides.
- The call is fully permissionless — `verify_transaction_inclusion_v2` has no `#[private]` or role guard.
- The only prerequisite is that the target block is within the GC window and on the canonical chain, both of which are normal operating conditions.

---

### Recommendation

`compute_root_from_merkle_proof` must reject a `transaction_position` that is at or beyond the first "virtual" duplicate slot. Concretely, after computing the root the caller should also verify that the supplied position is strictly less than the number of leaves at the bottom level, which equals `2^(proof.len())` only when the tree is full. The standard fix is to require that the proof does **not** contain the leaf's own hash as its first sibling (i.e., `proof[0] != tx_id`) when `tx_index` is even, which is the exact condition that arises from the duplicate-last-leaf construction. Alternatively, require callers to supply the total transaction count and reject any `tx_index >= tx_count`.

---

### Proof of Concept

```rust
// 3-tx block: [coinbase, T1, T2]
// Real proof for T2 at index 2: proof = [T2, H(coinbase, T1)]
// Attacker submits the same proof with tx_index = 3

let args = ProofArgsV2 {
    tx_id:                  T2,
    tx_block_blockhash:     block_hash,
    tx_index:               3,          // nonexistent position
    merkle_proof:           vec![T2, H_coinbase_T1],
    coinbase_tx_id:         coinbase,
    coinbase_merkle_proof:  vec![T1_hash, H_T2_T2],  // real coinbase proof
    confirmations:          1,
};
// verify_transaction_inclusion_v2(args) == true
// Same call with tx_index = 2 also returns true.
// Bridge keyed on (tx_id, block_hash, tx_index) processes the withdrawal twice.
``` [3](#0-2) [4](#0-3)

### Citations

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
