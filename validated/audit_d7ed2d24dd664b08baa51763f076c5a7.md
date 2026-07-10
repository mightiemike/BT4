### Title
Attacker-Controlled `coinbase_tx_id` Bypasses 64-Byte Forgery Protection in `verify_transaction_inclusion_v2` — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` accepts a caller-supplied `coinbase_tx_id` and uses it directly in a Merkle-root trust check without validating it against the actual coinbase transaction hash. An unprivileged NEAR caller can supply an internal Merkle-tree node as `coinbase_tx_id`, satisfy the root check with a crafted proof, and simultaneously pass a second internal node as `tx_id` — causing the function to return `true` for a transaction that does not exist on Bitcoin.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to mitigate the well-known 64-byte transaction Merkle-proof forgery (documented in the `verify_transaction_inclusion` deprecation notice). The intended protection is: require a coinbase proof of the same depth as the transaction proof, so that the transaction proof must reach a genuine leaf.

The implementation is:

```rust
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),   // ← fully attacker-controlled
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
```

`coinbase_tx_id` is taken verbatim from `ProofArgsV2` with no validation that it equals the hash of the block's actual coinbase transaction. The check only verifies that *some* hash, placed at position 0 with *some* sibling list, produces the stored Merkle root. Because the caller controls both `coinbase_tx_id` and `coinbase_merkle_proof`, they can substitute any internal node of the real Merkle tree and construct a matching proof — exactly the same substitution the protection was meant to prevent.

The analog to H-04 is direct: just as `QuickAccManager.isValidSignature` blindly trusted the caller-supplied `id` for its privilege lookup, `verify_transaction_inclusion_v2` blindly trusts the caller-supplied `coinbase_tx_id` for its root check. In both cases a singleton contract accepts an unverified identity/hash from the caller and uses it as the anchor of a security-critical comparison.

---

### Impact Explanation

Any NEAR contract that calls `verify_transaction_inclusion_v2` to gate a cross-chain action (e.g., a bridge crediting a deposit, a DEX releasing collateral) can be deceived into accepting a fabricated Bitcoin transaction inclusion proof. The attacker does not need to broadcast any Bitcoin transaction; they only need knowledge of the Merkle tree of any already-confirmed block that is stored in the light client's mainchain. The result is proof-verification forgery: the contract returns `true` for a `tx_id` that is an internal Merkle node, not a real transaction.

---

### Likelihood Explanation

The exploit requires no privileged role, no mining, and no cryptographic brute-force. The attacker only needs:
1. A block already accepted by the light client (publicly observable on-chain).
2. The Merkle tree of that block (obtainable from any Bitcoin full node or block explorer).
3. The ability to call `verify_transaction_inclusion_v2` — a public, unpermissioned entry point.

All three conditions are trivially satisfied by any external actor.

---

### Recommendation

Remove `coinbase_tx_id` from `ProofArgsV2` as a caller-supplied field. Instead, require the caller to supply the raw coinbase transaction bytes, compute its hash inside the contract, and use that computed hash as the anchor of the coinbase Merkle proof. This mirrors the H-04 mitigation: replace the attacker-controlled identity with a value derived from a trusted, contract-internal source (`msg.sender` in the Solidity case; the on-chain-computed coinbase hash here).

---

### Proof of Concept

Consider a confirmed Bitcoin block whose Merkle tree has four transactions with leaf hashes `L0, L1, L2, L3`:

```
I01 = hash(L0, L1)
I23 = hash(L2, L3)
R   = hash(I01, I23)   ← stored as merkle_root in the light client
```

The attacker calls `verify_transaction_inclusion_v2` with:

| Field | Value | Role |
|---|---|---|
| `tx_block_blockhash` | hash of the real block | passes mainchain lookup |
| `coinbase_tx_id` | `I01` (internal node, **not** the real coinbase hash) | anchor of fake coinbase proof |
| `coinbase_merkle_proof` | `[I23]` | `hash(I01, I23) = R` ✓ |
| `tx_id` | `I23` (internal node, **not** a real transaction) | the forged "transaction" |
| `merkle_proof` | `[I01]` | `hash(I01, I23) = R` ✓ |
| `tx_index` | `1` | position 1 is odd → `hash(I01, I23) = R` |
| `confirmations` | `1` | satisfied by any confirmed block |

Step-by-step execution:

1. **Length check**: `coinbase_merkle_proof.len() == merkle_proof.len()` → `1 == 1` ✓
2. **Coinbase root check**: `compute_root_from_merkle_proof(I01, 0, [I23])` → position 0 is even → `hash(I01, I23) = R == merkle_root` ✓
3. **Transaction root check** (inside `verify_transaction_inclusion`): `compute_root_from_merkle_proof(I23, 1, [I01])` → position 1 is odd → `hash(I01, I23) = R == merkle_root` ✓
4. Function returns **`true`** — `I23` is accepted as a valid included transaction, even though no such transaction exists.

The root cause is in `verify_transaction_inclusion_v2` at the `coinbase_tx_id` parameter acceptance: [1](#0-0) 

The `coinbase_tx_id` field that flows in unchecked is declared in `ProofArgsV2`: [2](#0-1) 

The `compute_root_from_merkle_proof` function that accepts any hash at any position without leaf-vs-node distinction: [3](#0-2)

### Citations

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

**File:** btc-types/src/contract_args.rs (L26-36)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgsV2 {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub coinbase_tx_id: H256,
    pub coinbase_merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
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
