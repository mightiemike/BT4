### Title
Duplicate-Leaf Ghost-Index Bypass in `verify_transaction_inclusion_v2` Accepts Nonexistent `tx_index` for Odd-Length Merkle Trees — (`contract/src/lib.rs`, `merkle-tools/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` can be made to return `true` for a `tx_index` that does not exist in the block's transaction list. For any Litecoin block with an odd number of transactions N, an attacker can supply `tx_index = N` (one past the last real leaf) together with the legitimate proof for `tx[N-1]`, and the root computed by `compute_root_from_merkle_proof` is identical to the real Merkle root. The coinbase-proof length-equality guard passes because both proofs have the same depth. No privilege is required; the call is a public view function.

---

### Finding Description

**Root cause — `compute_root_from_merkle_proof` has no bounds check on `transaction_position`.** [1](#0-0) 

The function only uses `transaction_position` to decide left/right at each level (`% 2`) and then halves it (`/= 2`). It never validates that the position is within the actual tree size.

**Why the ghost index produces the correct root.**

For a 3-tx block the Merkle tree is built as:

```
Level 0 (leaves):  tx[0]   tx[1]   tx[2]   tx[2]   ← tx[2] duplicated
Level 1:           h01             h22
Root:              hash(h01, h22)
```

The legitimate proof for `tx[2]` at position 2 is `[tx[2], h01]`:

| step | position | parity | operation |
|------|----------|--------|-----------|
| 1 | 2 | even | `hash(tx[2], proof[0]=tx[2])` → h22; pos→1 |
| 2 | 1 | odd  | `hash(proof[1]=h01, h22)` → root; pos→0 |

Now feed the **same proof** with `tx_index = 3`:

| step | position | parity | operation |
|------|----------|--------|-----------|
| 1 | 3 | **odd** | `hash(proof[0]=tx[2], tx[2])` → h22; pos→1 |
| 2 | 1 | odd  | `hash(proof[1]=h01, h22)` → root; pos→0 |

Both paths produce the same `h22` at step 1 (because `hash(tx[2], tx[2]) == hash(tx[2], tx[2])`), and therefore the same root. The ghost position 3 is arithmetically indistinguishable from position 2 under this proof.

**How `verify_transaction_inclusion_v2` is bypassed.** [2](#0-1) 

1. **Length check** (`merkle_proof.len() == coinbase_merkle_proof.len()`): both proofs have depth `ceil(log₂(3)) = 2` — passes.
2. **Coinbase check**: `compute_root_from_merkle_proof(tx[0], 0, coinbase_proof) == merkle_root` — passes with the real coinbase proof.
3. **Delegation**: `args.into()` preserves the attacker-supplied `tx_index = 3` verbatim. [3](#0-2) 

4. **Inner check** in `verify_transaction_inclusion`: `compute_root_from_merkle_proof(tx[2], 3, [tx[2], h01]) == merkle_root` — passes as shown above. [4](#0-3) 

There is no guard anywhere that rejects `tx_index >= N`.

---

### Impact Explanation

The contract returns `true` for the statement "transaction `tx[2]` is included at index 3 in this block," which is false — index 3 does not exist in a 3-tx block. Any downstream consumer that relies on the verified `tx_index` (e.g., to confirm a transaction is not the coinbase, to enforce ordering invariants, or to map an index to a specific output) receives a wrong-index attestation from a function that is explicitly designed to be the authoritative inclusion oracle. This matches the scoped Critical impact of "wrong index" inclusion claim.

The same pattern generalises to any odd N: ghost index N always aliases to the proof for index N-1.

---

### Likelihood Explanation

The call is a public, unprivileged NEAR view function. The attacker only needs a real Litecoin block with an odd transaction count (the majority of blocks), the real coinbase hash, and the real last-transaction hash. All of these are publicly available on-chain. No key material, relayer role, or privileged access is required.

---

### Recommendation

Add an explicit upper-bound check on `tx_index` before calling `compute_root_from_merkle_proof`. The proof length encodes the tree depth, so the maximum valid index is `2^proof_len - 1`, but a tighter bound derived from the actual transaction count is preferable. At minimum, reject any `tx_index >= 2^merkle_proof.len()` or, if the block's transaction count is available, reject `tx_index >= tx_count`. Additionally, `compute_root_from_merkle_proof` itself should validate that `transaction_position < (1 << merkle_proof.len())` to prevent this class of ghost-index abuse at the library level.

---

### Proof of Concept

Build a 3-tx Litecoin block with transactions `[cb, tx1, tx2]`. Compute:
- `coinbase_proof = [tx1_hash, hash(hash(tx2,tx2))]` — valid proof for `cb` at index 0, depth 2
- `tx_proof = [tx2_hash, hash(cb, tx1)]` — valid proof for `tx2` at index 2, depth 2

Call `verify_transaction_inclusion_v2` with:
```
tx_id              = tx2_hash
tx_block_blockhash = <real block hash>
tx_index           = 3          ← ghost index, does not exist
merkle_proof       = tx_proof   ← proof for index 2
coinbase_tx_id     = cb_hash
coinbase_merkle_proof = coinbase_proof
confirmations      = 0
```

Expected (correct) result: `false` — index 3 is out of bounds.
Actual result: **`true`** — the duplicate-leaf arithmetic makes both index 2 and index 3 produce the same Merkle root with the same proof.

### Citations

**File:** merkle-tools/src/lib.rs (L34-51)
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
```

**File:** contract/src/lib.rs (L315-322)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
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

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            tx_block_blockhash: args.tx_block_blockhash,
            tx_index: args.tx_index,
            merkle_proof: args.merkle_proof,
            confirmations: args.confirmations,
        }
    }
```
