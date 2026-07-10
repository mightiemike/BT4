### Title
Fake `coinbase_tx_id` Bypasses 64-Byte Internal-Node Mitigation in `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

The coinbase proof check in `verify_transaction_inclusion_v2` only verifies that *some* value at position 0 with *some* proof reconstructs the Merkle root. It never verifies that `coinbase_tx_id` is the actual coinbase transaction. An unprivileged caller can supply an internal Merkle node as both `coinbase_tx_id` and `tx_id`, with identical proofs of equal length, causing the function to return `true` for a non-existent transaction.

---

### Finding Description

The mitigation design intent is: the coinbase transaction is always at tree depth D, so requiring `merkle_proof.len() == coinbase_merkle_proof.len()` forces the target `tx_id` to also be at depth D, making it impossible to prove an internal node (which lives at depth < D) with a proof of length D.

This reasoning breaks because the code never binds `coinbase_tx_id` to the actual coinbase transaction. The only check is:

```rust
// contract/src/lib.rs:358-365
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [1](#0-0) 

The attacker is free to supply any `coinbase_tx_id` that satisfies this equation — including an internal Merkle node — with a proof shorter than the real coinbase proof. The length equality check:

```rust
// contract/src/lib.rs:348-351
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    ...
);
``` [2](#0-1) 

is trivially satisfied by making both proofs identical.

`compute_root_from_merkle_proof` is a pure computation with no guards:

```rust
// merkle-tools/src/lib.rs:34-51
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 { ... }
``` [3](#0-2) 

The `ProofArgsV2 → ProofArgs` conversion passes `tx_id` and `merkle_proof` through unchanged with no additional validation: [4](#0-3) 

---

### Impact Explanation

An attacker can make `verify_transaction_inclusion_v2` return `true` for `tx_id = N01` where `N01` is an internal Merkle node (e.g., `hash(tx0, tx1)`), not a real transaction. Any downstream system that trusts this return value — e.g., a bridge, payment processor, or cross-chain relay — will accept a fabricated transaction inclusion proof. This is the exact class of forgery the v2 mitigation was designed to prevent.

---

### Likelihood Explanation

The function is a public, unprivileged NEAR view call (`#[pause]` only, no role restriction). No privileged access, leaked keys, or social engineering is required. The attacker only needs knowledge of the block's Merkle tree, which is public on-chain data. The exploit is deterministic and requires no brute force.

---

### Recommendation

Bind `coinbase_tx_id` to the actual coinbase transaction stored in the block header, or fetch it from the block data and compare. Concretely, the contract must verify that `args.coinbase_tx_id` equals the known coinbase txid for the block (e.g., stored alongside the header, or derived from the block's coinbase commitment). Without this binding, the length-equality check provides no security guarantee because the attacker controls the "coinbase" anchor.

---

### Proof of Concept

For a canonical 4-tx block with transactions `[tx0, tx1, tx2, tx3]`:

```
N01  = hash(tx0, tx1)
N23  = hash(tx2, tx3)
root = hash(N01, N23)
```

Attacker calls `verify_transaction_inclusion_v2` with:

```
coinbase_tx_id       = N01          // internal node, NOT the real coinbase tx0
coinbase_merkle_proof = [N23]       // length 1
tx_id                = N01          // same internal node
tx_index             = 0
merkle_proof         = [N23]        // length 1 — equal to coinbase proof length ✓
confirmations        = 0
```

Execution trace:

1. **Length check**: `1 == 1` ✓
2. **Coinbase check**: `compute_root_from_merkle_proof(N01, 0, [N23])` → `hash(N01, N23)` = `root` ✓
3. **`verify_transaction_inclusion`**: `compute_root_from_merkle_proof(N01, 0, [N23])` = `root` ✓
4. **Returns `true`** for `tx_id = N01`, an internal node that is not a transaction.

The block need not have exactly 4 transactions; any block where an internal node `N` at level 1 satisfies `hash(N, sibling) == root` (i.e., any block with ≥ 2 transactions) is exploitable with the same pattern.

### Citations

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
