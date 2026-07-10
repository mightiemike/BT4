### Title
Caller-Supplied `merkle_proof` Array Not Linked to Block's Transaction Set Enables Proof Forgery via Internal Merkle Node — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` accepts a caller-supplied `merkle_proof: Vec<H256>` and `tx_index: u64` that are never validated against the block's actual transaction count or Merkle tree depth. The only constraint is that the computed root must equal the stored `merkle_root`. Because internal Merkle tree nodes are themselves part of the tree computation, an attacker can supply an internal node hash as `tx_id` with a shorter crafted proof that still reaches the correct root, causing the function to return `true` for a transaction that does not exist in the block.

---

### Finding Description

`verify_transaction_inclusion` iterates over the caller-supplied `merkle_proof` array in `compute_root_from_merkle_proof`: [1](#0-0) 

The loop runs for exactly `merkle_proof.len()` iterations, using `current_position` (derived from the caller-supplied `tx_index`) to decide left/right ordering at each level. There is no check that:

- `merkle_proof.len()` equals the expected tree depth for the block (i.e., `ceil(log2(tx_count))`).
- `tx_index` is less than the actual number of transactions in the block.

The contract stores no transaction count per block — only the `merkle_root` inside the `LightHeader`: [2](#0-1) 

The sole validation in `verify_transaction_inclusion` is: [3](#0-2) 

Because an internal Merkle node at level *k* is itself the result of hashing two child nodes, it is a valid intermediate value in the tree. An attacker can treat it as a "leaf" and supply a proof of length `(depth − k)` that correctly traverses from that node to the root. The final equality check passes, and the function returns `true` for a `tx_id` that is not a real transaction.

The only guard against the trivially degenerate case (empty proof, `tx_id` = root) is: [4](#0-3) 

This does not prevent a non-empty proof rooted at an internal node.

The function is marked deprecated but remains deployed and callable by any unprivileged NEAR account: [5](#0-4) 

The `#[pause]` attribute is the only gate; no role or relayer restriction applies.

---

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` to authorize an action (cross-chain bridge unlock, payment settlement, SPV-gated logic) can be deceived into accepting a proof for a Bitcoin transaction that was never broadcast or confirmed. The attacker does not need to mine a block or break SHA-256d — they only need to read the public Merkle tree of any block already accepted into the light client's mainchain and derive an internal node hash.

The corrupted invariant is: *`verify_transaction_inclusion` must return `true` only for hashes of actual leaf transactions in the block*. This invariant is broken.

---

### Likelihood Explanation

**Medium.** Every Bitcoin block's full transaction list is public. An attacker can compute all internal Merkle node hashes offline for any block stored in the light client's mainchain, then immediately call `verify_transaction_inclusion` with a crafted `(tx_id=internal_node, tx_index, merkle_proof)` triple. No privileged access, no key material, and no cryptographic computation beyond standard SHA-256d (which is just reading public data) is required. The only prerequisite is that the target block is in the contract's mainchain with sufficient confirmations.

---

### Recommendation

1. **Enforce proof-length consistency**: require `tx_index < (1u64 << merkle_proof.len())` so that the claimed position is reachable at the claimed depth.
2. **Enforce depth consistency with coinbase**: adopt the same coinbase-length-equality check used in `verify_transaction_inclusion_v2`: [6](#0-5) 

3. **Deprecation enforcement**: consider removing `verify_transaction_inclusion` entirely or adding an unconditional `panic!` so callers are forced to migrate to `verify_transaction_inclusion_v2`.

---

### Proof of Concept

Assume a Bitcoin block with 4 transactions `[T0, T1, T2, T3]` is stored in the light client's mainchain.

```
Level 2 (root):  Root = hash(N01, N23)
Level 1:         N01  = hash(T0, T1)     N23 = hash(T2, T3)
Level 0 (leaf):  T0   T1                 T2   T3
```

Attacker calls `verify_transaction_inclusion` with:

| Field | Value |
|---|---|
| `tx_id` | `N01` (internal node — not a real transaction) |
| `tx_block_blockhash` | real block hash in mainchain |
| `tx_index` | `0` (N01 is at position 0 in level 1) |
| `merkle_proof` | `[N23]` (one element — path from N01 to root) |
| `confirmations` | `1` |

Execution inside `compute_root_from_merkle_proof`:

```
current_hash     = N01
current_position = 0

iteration 1 (proof_hash = N23):
  position 0 is even → current_hash = hash(N01, N23) = Root
  current_position = 0

return Root
```

`Root == header.block_header.merkle_root` → **function returns `true`** for the non-existent transaction `N01`. [7](#0-6) [8](#0-7)

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

**File:** btc-types/src/btc_header.rs (L10-23)
```rust
pub struct Header {
    /// Block version, now repurposed for soft fork signalling.
    pub version: i32,
    /// Reference to the previous block in the chain.
    pub prev_block_hash: H256,
    /// The root hash of the merkle tree of transactions in the block.
    pub merkle_root: H256,
    /// The timestamp of the block, as claimed by the miner.
    pub time: u32,
    /// The target value below which the blockhash must lie.
    pub bits: u32,
    /// The nonce, selected to obtain a low enough blockhash.
    pub nonce: u32,
}
```

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L315-323)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L348-351)
```rust
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );
```
