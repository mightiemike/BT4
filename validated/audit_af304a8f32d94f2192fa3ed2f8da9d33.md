### Title
Deprecated `verify_transaction_inclusion` Publicly Callable Without Coinbase Guard, Accepts Internal Merkle Node as Valid `tx_id` — (`contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` method remains a live, publicly callable NEAR contract entry point with no access control beyond a pause gate. It accepts a raw `ProofArgs` struct that contains no coinbase proof fields, and passes `tx_id` directly into `compute_root_from_merkle_proof` without any check that the hash corresponds to a leaf transaction. An unprivileged caller can supply an internal Merkle tree node as `tx_id` with a shortened proof path and receive `true`.

---

### Finding Description

`verify_transaction_inclusion` is declared `pub` with only `#[pause]` and `#[deprecated]` attributes: [1](#0-0) 

`#[deprecated]` is a Rust compile-time lint — it does **not** remove the method from the deployed WASM binary or restrict on-chain calls. Any NEAR account can invoke it directly via a function call action. There is no `#[private]`, no owner check, and no role guard.

The function's only validation is a confirmation-count check and a non-empty proof check, then it delegates directly to: [2](#0-1) 

`compute_root_from_merkle_proof` treats `transaction_hash` as an opaque 32-byte value and iterates the proof path without any leaf-vs-internal-node distinction: [3](#0-2) 

The `From<ProofArgsV2> for ProofArgs` conversion that `verify_transaction_inclusion_v2` uses internally drops `coinbase_tx_id` and `coinbase_merkle_proof`: [4](#0-3) 

`verify_transaction_inclusion_v2` enforces the coinbase guard **before** calling the deprecated function: [5](#0-4) 

But that guard is entirely skippable because the deprecated entry point is still independently reachable.

The contract's own documentation acknowledges this exact behavior: [6](#0-5) 

---

### Impact Explanation

An attacker can call `verify_transaction_inclusion` with:
- `tx_id` = an internal Merkle node hash (e.g., `hash(tx0 ‖ tx1)` at level 1 of a 4-tx block)
- `tx_index` = the position of that node at its level (e.g., `0`)
- `merkle_proof` = the remaining siblings from that level up to the root (one element shorter than a full leaf proof)
- `tx_block_blockhash` = any confirmed canonical block hash
- `confirmations` = 0

`compute_root_from_merkle_proof` will correctly reconstruct the Merkle root from the internal node and return `true`. The caller receives a valid-looking inclusion proof for a transaction that does not exist. Any downstream system (bridge, oracle, settlement layer) that calls this entry point and trusts its boolean result is deceived into accepting a fabricated transaction as confirmed.

---

### Likelihood Explanation

The attack requires no privileges, no leaked keys, and no special chain state. The attacker only needs:
1. A block already in the contract's `headers_pool` (any canonical block will do).
2. Knowledge of that block's transaction list (publicly available from any Bitcoin node).
3. The ability to send a NEAR function call — available to any account.

The deprecated function is live in the current deployment. The 64-byte internal-node forgery technique is well-documented (referenced in the contract's own comments at line 268).

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or gate it with `#[private]` so it is only callable by the contract itself (i.e., only reachable through `verify_transaction_inclusion_v2`). Marking a NEAR contract method `#[deprecated]` does not restrict external calls; the method must be made non-callable or removed from the WASM export.

---

### Proof of Concept

Given a block with 4 transactions `[tx0, tx1, tx2, tx3]` already stored in the contract:

```
Level 0 (leaves): tx0   tx1   tx2   tx3
Level 1 (nodes):  N01 = hash(tx0‖tx1)   N23 = hash(tx2‖tx3)
Level 2 (root):   root = hash(N01‖N23)
```

Call `verify_transaction_inclusion` with:
```json
{
  "tx_id":              "<N01>",
  "tx_block_blockhash": "<block_hash>",
  "tx_index":           0,
  "merkle_proof":       ["<N23>"],
  "confirmations":      0
}
```

`compute_root_from_merkle_proof(N01, 0, [N23])` computes `hash(N01 ‖ N23) == root` → returns **`true`**.

`N01` is not a transaction; it is an internal node. No real transaction with that ID exists in the block. The function has falsely certified its inclusion.

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
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

**File:** contract/src/lib.rs (L317-322)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
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

**File:** merkle-tools/src/lib.rs (L33-51)
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
