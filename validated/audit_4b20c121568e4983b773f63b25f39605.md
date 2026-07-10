### Title
`verify_transaction_inclusion_v2` Coinbase Proof Bypass via Duplicate Proof Inputs — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` was introduced to mitigate the 64-byte transaction Merkle proof forgery vulnerability. It does so by requiring a separate coinbase proof alongside the target transaction proof. However, the function never checks that the coinbase proof is actually for a *different* transaction than the target. An unprivileged caller can supply the same `tx_id`, `tx_index = 0`, and identical proof arrays for both the coinbase and the target, causing both checks to pass with a single proof. The 64-byte forgery protection is completely bypassed.

---

### Finding Description

`verify_transaction_inclusion_v2` performs three checks before returning `true`:

1. Both proof arrays have equal length.
2. `compute_root_from_merkle_proof(coinbase_tx_id, 0, coinbase_merkle_proof) == merkle_root`
3. `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == merkle_root` [1](#0-0) 

There is no constraint that `coinbase_tx_id != tx_id`, that `tx_index != 0`, or that the two proof arrays differ. When a caller sets `coinbase_tx_id = tx_id`, `coinbase_merkle_proof = merkle_proof`, and `tx_index = 0`, checks (2) and (3) become identical computations over the same inputs. Both trivially pass, and the function returns `true`.

The `ProofArgsV2` struct accepts all these fields from the caller with no restrictions: [2](#0-1) 

The coinbase proof was designed to guarantee that the Merkle tree contains at least one real transaction (the coinbase at index 0), which is the standard mitigation for the 64-byte internal-node forgery attack. By allowing the same proof to satisfy both the coinbase check and the target-transaction check, the mitigation is rendered inert.

This is structurally identical to the reported vulnerability: the external report's `canExecTakeOrder` checked that each taker item was *within scope* and that the *count matched*, but never verified the set was *distinct*—allowing a duplicate item to satisfy both slots. Here, `verify_transaction_inclusion_v2` checks that each proof *reaches the Merkle root* and that the *lengths match*, but never verifies the two proofs are *for different transactions*—allowing one proof to satisfy both slots.

---

### Impact Explanation

Any NEAR contract or off-chain consumer that calls `verify_transaction_inclusion_v2` and acts on a `true` result (e.g., a cross-chain bridge releasing funds, a payment verifier crediting a deposit) can be deceived. An attacker who can craft a 64-byte value whose double-SHA256 hash equals an internal Merkle node can forge a proof that an arbitrary "transaction" was included in a real Bitcoin block, and `verify_transaction_inclusion_v2` will return `true`. The deprecated `verify_transaction_inclusion` explicitly documents this risk in its `# Warning` comment: [3](#0-2) 

`verify_transaction_inclusion_v2` was the designated fix for that warning, but the fix is incomplete.

---

### Likelihood Explanation

The function is a public, unpermissioned NEAR entry point callable by any account. No privileged role, staking, or special access is required. The attacker only needs to supply a crafted `ProofArgsV2` struct with `coinbase_tx_id = tx_id`, `coinbase_merkle_proof = merkle_proof`, and `tx_index = 0`. The 64-byte internal-node preimage construction is a known, documented Bitcoin attack with published tooling.

---

### Recommendation

Add an explicit guard at the top of `verify_transaction_inclusion_v2` that enforces the coinbase and target transaction are distinct:

```rust
require!(
    args.tx_index != 0,
    "tx_index 0 is reserved for the coinbase; use a non-zero index for target transactions"
);
```

Or, if coinbase self-verification must be supported, enforce that the proof inputs are not identical:

```rust
require!(
    args.coinbase_tx_id != args.tx_id || args.coinbase_merkle_proof != args.merkle_proof,
    "coinbase proof must differ from the target transaction proof"
);
```

The stronger fix is the `tx_index != 0` guard, because it closes the structural gap: the coinbase is always at index 0, so any legitimate non-coinbase transaction must have `tx_index > 0`, making the two proofs necessarily distinct.

---

### Proof of Concept

Assume a real Bitcoin block `B` with Merkle root `R` and at least one transaction. An attacker constructs a 64-byte blob `T` such that `double_sha256(T) == R` (the known 64-byte forgery preimage for a single-transaction block where `R` is also the leaf). The attacker calls:

```rust
verify_transaction_inclusion_v2(ProofArgsV2 {
    tx_id:                  T_hash,       // hash of the crafted 64-byte blob
    tx_block_blockhash:     B_hash,       // real block in the contract's mainchain
    tx_index:               0,
    merkle_proof:           vec![],       // empty: single-tx block, root == leaf
    coinbase_tx_id:         T_hash,       // SAME as tx_id
    coinbase_merkle_proof:  vec![],       // SAME as merkle_proof
    confirmations:          1,
})
```

Step-by-step execution:

1. `merkle_proof.len() == coinbase_merkle_proof.len()` → `0 == 0` ✓
2. `compute_root_from_merkle_proof(T_hash, 0, [])` returns `T_hash`; if `T_hash == R` → coinbase check passes ✓
3. `compute_root_from_merkle_proof(T_hash, 0, [])` returns `T_hash == R` → tx check passes ✓
4. Function returns `true`. [1](#0-0) [4](#0-3) 

The result is a verified-`true` inclusion proof for a transaction that does not exist in the Bitcoin block, with no privileged access required.

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
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

**File:** btc-types/src/contract_args.rs (L28-36)
```rust
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
