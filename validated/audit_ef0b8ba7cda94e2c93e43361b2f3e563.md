### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (`File: contract/src/lib.rs`)

---

### Summary

The `verify_transaction_inclusion` function was deprecated in favour of `verify_transaction_inclusion_v2` specifically because it is vulnerable to the 64-byte transaction Merkle proof forgery attack. However, the old function was never removed or access-restricted; it remains a `pub` NEAR entry point callable by any unprivileged account. An attacker can supply an internal Merkle-tree node hash as `tx_id` and obtain a `true` SPV-inclusion result for a Bitcoin transaction that does not exist.

---

### Finding Description

`verify_transaction_inclusion` is marked `#[deprecated]` but carries no `#[private]` guard and is only gated by `#[pause]`, which is a liveness control, not an access-control restriction. [1](#0-0) 

The function's own doc-comment acknowledges the flaw: [2](#0-1) 

The underlying `compute_root_from_merkle_proof` in `merkle-tools` performs a pure positional hash walk with no constraint that the leaf must be a transaction rather than an internal node: [3](#0-2) 

The security fix — coinbase-proof validation — was added only in `verify_transaction_inclusion_v2`: [4](#0-3) 

Because the old function was not removed, the fix is absent for any caller that invokes `verify_transaction_inclusion` directly.

---

### Impact Explanation

A downstream NEAR contract or user that calls `verify_transaction_inclusion` can be made to accept a fraudulent SPV proof. The attacker supplies an internal Merkle-tree node hash as `tx_id`; `compute_root_from_merkle_proof` correctly reconstructs the Merkle root from that node, so the comparison with `header.block_header.merkle_root` passes and the function returns `true`. The corrupted proof result is: a non-existent Bitcoin transaction is certified as confirmed on-chain, which can trigger fund releases, bridge operations, or other irreversible cross-chain actions in any contract that consumes this verification result.

---

### Likelihood Explanation

The attack requires only knowledge of a real Bitcoin block's Merkle tree structure (publicly available) and the ability to call a NEAR contract method — no privileged role, no leaked key, no social engineering. The function is permanently reachable as long as the contract is not paused. Any actor who discovers the deprecated entry point can exploit it.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public interface entirely, or add `#[private]` to make it uncallable from outside the contract. All external callers must be migrated to `verify_transaction_inclusion_v2`. If backward compatibility is required during a transition period, the function body should be replaced with an unconditional `env::panic_str("use verify_transaction_inclusion_v2")`.

---

### Proof of Concept

1. Let block `B` be any block stored in the contract's mainchain with a known Merkle tree of ≥ 2 transactions `[T0, T1, ...]`.
2. Compute `N = double_sha256(T0 || T1)` — this is the internal node at depth 1.
3. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash, not a real txid)
   - `tx_block_blockhash = B`
   - `tx_index = 0` (even position → the verifier hashes `N` with the next sibling)
   - `merkle_proof` = the remaining path from depth 1 to the root
4. `compute_root_from_merkle_proof(N, 0, proof)` reconstructs the correct Merkle root of `B`.
5. The function returns `true`, certifying a non-existent transaction as confirmed.

The entry point is `verify_transaction_inclusion` at: [5](#0-4) 

The root cause is the absence of coinbase-proof validation in that path, which `verify_transaction_inclusion_v2` addresses at: [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L283-323)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
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
