### Title
Overly Restrictive Empty-Proof Guard Incorrectly Rejects Valid Single-Transaction Block SPV Proofs — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` contains a hard `require!(!args.merkle_proof.is_empty())` guard that unconditionally rejects any proof with an empty Merkle path. For a Bitcoin block containing exactly one transaction, the Merkle proof is legitimately empty (the Merkle root equals the transaction hash directly). The underlying `compute_root_from_merkle_proof` handles this case correctly, but the guard fires first and panics, making it impossible for any caller to verify a transaction in a single-transaction block. Because `verify_transaction_inclusion_v2` delegates to the deprecated function after its own coinbase check, it inherits the same defect.

---

### Finding Description

In `contract/src/lib.rs`, `verify_transaction_inclusion` contains:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [1](#0-0) 

The guard at line 315 is the root cause. In Bitcoin, when a block contains only one transaction (the coinbase), the Merkle tree has a single leaf. The Merkle root is that leaf's hash, and no sibling hashes are needed — the proof is an empty `Vec`. The `compute_root_from_merkle_proof` function in `merkle-tools/src/lib.rs` handles this correctly: with an empty `merkle_proof`, the loop body never executes and `current_hash` (the transaction hash) is returned unchanged. [2](#0-1) 

The guard therefore rejects a case that the downstream computation would resolve correctly, mirroring the pattern in the external report exactly: an unnecessary type/shape restriction that blocks a legitimate operation the rest of the code is already equipped to handle.

`verify_transaction_inclusion_v2` first validates the coinbase proof (which also passes with an empty proof for a single-transaction block), then calls `verify_transaction_inclusion` via `self.verify_transaction_inclusion(args.into())`, inheriting the panic: [3](#0-2) 

The length-equality check at line 349 requires `merkle_proof.len() == coinbase_merkle_proof.len()`, so both would be empty for a single-transaction block, and the coinbase check at lines 358–365 would pass (empty proof returns `coinbase_tx_id` which equals `merkle_root`). The call then falls through to `verify_transaction_inclusion`, which panics. [4](#0-3) 

---

### Impact Explanation

Any NEAR contract or user that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to prove a transaction in a Bitcoin block that contains only one transaction will receive a panic (`"Merkle proof is empty"`) instead of a correct `true` result. This corrupts the SPV verification result for a class of valid Bitcoin blocks, causing the light client to report false negatives. Downstream contracts that rely on the return value for settlement, bridging, or custody decisions will incorrectly treat valid proofs as failures.

---

### Likelihood Explanation

Single-transaction Bitcoin blocks (containing only the coinbase) occur regularly, especially during periods of low mempool activity. Any relayer or user who submits such a block to the contract and then attempts to verify the coinbase transaction against it will trigger the bug deterministically. No special privileges are required; `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` are both public, unpermissioned endpoints callable by any NEAR account. [5](#0-4) [6](#0-5) 

---

### Recommendation

Remove the guard:

```rust
// DELETE this line:
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
```

`compute_root_from_merkle_proof` already handles the empty-proof case correctly by returning the transaction hash unchanged, which will equal the Merkle root for a single-transaction block. No additional logic is needed. [7](#0-6) 

---

### Proof of Concept

1. Submit a Bitcoin block header whose `merkle_root` equals a single transaction hash `T` (a real single-transaction block).
2. Call `verify_transaction_inclusion` with:
   - `tx_id = T`
   - `tx_block_blockhash` = hash of that block
   - `tx_index = 0`
   - `merkle_proof = []` (empty — correct for a single-transaction block)
   - `confirmations = 1`
3. The contract panics with `"Merkle proof is empty"` before reaching `compute_root_from_merkle_proof`.
4. The correct result would be `true`, since `compute_root_from_merkle_proof(T, 0, &[])` returns `T`, which equals `merkle_root`.

The same sequence via `verify_transaction_inclusion_v2` with `coinbase_tx_id = T`, `coinbase_merkle_proof = []`, `merkle_proof = []` passes the coinbase check and then panics at the same guard. [8](#0-7)

### Citations

**File:** contract/src/lib.rs (L287-288)
```rust
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
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

**File:** contract/src/lib.rs (L346-368)
```rust
    #[pause]
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
