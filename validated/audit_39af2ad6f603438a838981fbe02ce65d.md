### Title
Duplicate-Last-Leaf Position Ambiguity Allows Same Proof to Verify Two Distinct `tx_index` Values — (`merkle-tools/src/lib.rs::compute_root_from_merkle_proof`)

---

### Summary

`compute_root_from_merkle_proof` accepts a caller-supplied `transaction_position` with no upper-bound check. In any Bitcoin block whose transaction count is odd, the Merkle tree duplicates the last leaf. This makes the proof for the real last transaction (position `N-1`) mathematically identical to a proof for the phantom duplicate position `N`. An unprivileged caller can therefore pass `tx_index = N` with the same proof bytes and receive `true` from `verify_transaction_inclusion_v2`, even though no transaction occupies position `N`.

---

### Finding Description

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs`:

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
``` [1](#0-0) 

There is no check that `transaction_position < 2^(proof_depth)` or that it falls within the actual leaf count. The block header stored on-chain contains only the Merkle root, not the transaction count, so the contract has no way to enforce a bound. [2](#0-1) 

**Concrete arithmetic for a 3-transaction block** (`[T0, T1, T2]`):

Bitcoin pads the tree to even width: `[T0, T1, T2, T2]`.

| Level | Nodes |
|-------|-------|
| 0 (leaves) | T0, T1, T2, T2 |
| 1 | H(T0,T1), H(T2,T2) |
| root | H(H(T0,T1), H(T2,T2)) |

Proof for **position 2** (real):
- pos=2 (even) → sibling = T2 → hash = H(T2,T2), pos→1
- pos=1 (odd) → sibling = H(T0,T1) → hash = root

Proof for **position 3** (phantom):
- pos=3 (odd) → sibling = T2 → hash = H(T2,T2), pos→1
- pos=1 (odd) → sibling = H(T0,T1) → hash = root

Both produce the **identical proof vector** `[T2, H(T0,T1)]` and both return the correct root. `verify_transaction_inclusion_v2` returns `true` for both calls.

`verify_transaction_inclusion_v2` adds a coinbase-proof length check and a coinbase root check, but neither constrains `tx_index`: [3](#0-2) 

The coinbase proof is verified independently at position `0`; it says nothing about the maximum valid index for other transactions.

---

### Impact Explanation

A downstream bridge, mint, or unlock contract that calls `verify_transaction_inclusion_v2` and uses `(tx_id, tx_index)` as its replay-prevention key can be made to accept the same BTC transaction twice: once at the real index `N-1` and once at the phantom index `N`. The `tx_id` is the same real transaction in both calls — no cryptographic forgery is required. The attacker does not need any privileged role; the function is fully public.

The contract itself cannot be fixed without storing per-block transaction counts, which are absent from Bitcoin block headers.

---

### Likelihood Explanation

- Any Bitcoin block with an odd transaction count (roughly half of all blocks) is affected.
- The attacker needs only a valid block already in the light client's main chain and the real proof for the last transaction — both are public information.
- No key material, relayer compromise, or DAO role is required.
- The only prerequisite is that a downstream consumer uses `(tx_id, tx_index)` rather than `tx_id` alone as its deduplication key, which is a natural design choice when a bridge wants to distinguish coinbase outputs from regular outputs at the same index.

---

### Recommendation

1. **Document the invariant explicitly in `verify_transaction_inclusion_v2`**: add a NatSpec/doc comment stating that callers must independently verify `tx_index < tx_count` using the raw block data, because the on-chain proof cannot enforce this bound.
2. **Downstream bridges** must deduplicate on `tx_id` alone (or on the full raw transaction), never on `(tx_id, tx_index)`, because the index is not uniquely bound by the proof.
3. Optionally, accept `tx_count` as a caller-supplied parameter and verify `tx_index < tx_count` and `proof_depth == ceil(log2(tx_count))`. This does not require changing the stored state but does require the caller to supply the count honestly; it shifts the trust assumption rather than eliminating it.

---

### Proof of Concept

```rust
// 3-transaction block: [T0, T1, T2]
// Merkle root = H(H(T0,T1), H(T2,T2))
// Valid proof for T2 at index 2: [T2, H(T0,T1)]

let proof = vec![T2.clone(), hash_of_T0_T1];

// Real call — returns true (correct)
assert!(compute_root_from_merkle_proof(T2.clone(), 2, &proof) == merkle_root);

// Phantom call — also returns true (position 3 does not exist)
assert!(compute_root_from_merkle_proof(T2.clone(), 3, &proof) == merkle_root);

// verify_transaction_inclusion_v2 with tx_index=3 returns true,
// allowing a bridge to process T2 a second time under a different index key.
``` [1](#0-0) [4](#0-3)

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

**File:** contract/CLAUDE.md (L66-66)
```markdown
**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.
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
