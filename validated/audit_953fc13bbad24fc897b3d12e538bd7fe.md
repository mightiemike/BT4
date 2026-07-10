### Title
Forged Internal-Node Coinbase Bypasses 64-Byte Attack Mitigation in `verify_transaction_inclusion_v2` — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` is intended to defeat the 64-byte Merkle-node forgery attack by requiring a coinbase proof of the same depth as the transaction proof. However, the function never verifies that `coinbase_tx_id` is actually the leaf-level coinbase transaction (tx[0]). An attacker can supply an internal Merkle node as `coinbase_tx_id` with a shortened proof that still reconstructs the root at position 0, satisfying the coinbase guard while using a proof depth that is shallower than the real tree. The same internal node (or a sibling internal node) then trivially passes the transaction inclusion check at the same shortened depth, causing the function to return `true` for a value that is not a real transaction.

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

`compute_root_from_merkle_proof` is a pure hash-chain computation with no constraint that the starting value must be a leaf:

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
``` [2](#0-1) 

For a 4-transaction block with leaf hashes `tx[0..3]`, the tree is:

```
h01 = hash(tx[0], tx[1])
h23 = hash(tx[2], tx[3])
root = hash(h01, h23)
```

`compute_root_from_merkle_proof(h01, 0, [h23])` evaluates to `hash(h01, h23) = root`. The coinbase guard passes with `coinbase_tx_id = h01` — an internal node, not the actual coinbase transaction.

The only other guard is the equal-length check:

```rust
require!(
    args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
    "Coinbase merkle proof and transaction merkle proof should have the same length"
);
``` [3](#0-2) 

Both proofs are length 1, so this passes. The subsequent call to `verify_transaction_inclusion` then checks:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [4](#0-3) 

With `tx_id = h01`, `tx_index = 0`, `merkle_proof = [h23]`, this also returns `root`, so the function returns `true` for `h01` — an internal node that is not a real transaction.

---

### Impact Explanation

The entire purpose of `verify_transaction_inclusion_v2` is to prevent the 64-byte attack, where an attacker crafts a 64-byte value that is simultaneously a valid serialized transaction and a valid internal Merkle node. The coinbase proof is supposed to anchor the tree depth: if the real coinbase (a known leaf) requires a proof of depth D, then any claimed transaction must also use depth D, preventing a shorter proof that treats an internal node as a leaf.

This mitigation is entirely defeated. An attacker who knows the internal structure of any canonical block (all data is public) can:

1. Compute `h01 = hash(tx[0], tx[1])` from the block's transactions.
2. Call `verify_transaction_inclusion_v2` with `coinbase_tx_id = h01`, `coinbase_merkle_proof = [h23]`, `tx_id = h01`, `tx_index = 0`, `merkle_proof = [h23]`.
3. Receive `true` — the contract asserts that `h01` is a transaction included in the block.

Any downstream consumer of this function (e.g., a bridge, a payment verifier, a cross-chain protocol) that trusts its `true` return value can be deceived into accepting a forged transaction inclusion claim.

---

### Likelihood Explanation

The attack requires no privileges, no key compromise, and no special chain state. The attacker only needs:
- A canonical block already accepted by the light client (publicly observable).
- Knowledge of any two adjacent leaf hashes in that block (computable from public block data).

The call to `verify_transaction_inclusion_v2` is a public, unpermissioned NEAR view/call method with no access-control gate beyond the `#[pause]` flag (which is a liveness control, not a security control against this input). [5](#0-4) 

---

### Recommendation

The coinbase guard must verify that `coinbase_tx_id` is a genuine leaf-level node, not an internal node. The standard mitigation is to enforce that the proof depth matches the actual tree depth implied by the block's transaction count, or to require that `coinbase_tx_id` is the actual coinbase transaction by checking it against a stored or derivable value. Concretely:

- Require `coinbase_merkle_proof.len() == ceil(log2(tx_count))` where `tx_count` is derived from the block header or a trusted source, OR
- Require that `coinbase_tx_id` is explicitly the hash of the first transaction in the block (stored or passed and verified against a trusted source), OR
- Reject any `coinbase_tx_id` that is 64 bytes when serialized (the standard Bitcoin Core mitigation), noting that all valid txids are 32 bytes so this is already satisfied at the type level — the real fix is the depth check.

---

### Proof of Concept

For a 4-tx block with known transactions `tx[0], tx[1], tx[2], tx[3]` already submitted to the light client:

```
h01 = double_sha256(tx[0] || tx[1])
h23 = double_sha256(tx[2] || tx[3])
root = double_sha256(h01 || h23)   // == block header merkle_root ✓
```

Call `verify_transaction_inclusion_v2` with:
```
tx_id                = h01          // internal node, not a real transaction
tx_block_blockhash   = <valid block hash>
tx_index             = 0
merkle_proof         = [h23]        // length 1
coinbase_tx_id       = h01          // same internal node
coinbase_merkle_proof = [h23]       // length 1
confirmations        = 0
```

Execution trace:
1. Length check: `1 == 1` → passes.
2. Coinbase guard: `compute_root_from_merkle_proof(h01, 0, [h23])` = `hash(h01, h23)` = `root` → passes.
3. `verify_transaction_inclusion`: `compute_root_from_merkle_proof(h01, 0, [h23])` = `root` → returns `true`.

**Result:** The contract returns `true`, asserting that the internal node `h01` is a transaction included in the block. The 64-byte attack mitigation is fully bypassed.

### Citations

**File:** contract/src/lib.rs (L318-322)
```rust
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
