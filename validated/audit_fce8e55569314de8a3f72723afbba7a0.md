### Title
Coinbase-Proof Forgery-Mitigation Bypass via Direct Call to Deprecated `verify_transaction_inclusion` — (`File: contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion_v2` was introduced to close the 64-byte transaction Merkle proof forgery vulnerability by requiring a coinbase Merkle proof. However, the original `verify_transaction_inclusion` (v1) remains a fully public, externally callable NEAR function. Any unprivileged caller can invoke v1 directly, bypassing the coinbase proof check entirely and obtaining a `true` SPV result for a forged transaction.

### Finding Description
`verify_transaction_inclusion_v2` enforces a coinbase Merkle proof check before delegating to v1: [1](#0-0) 

This check is the sole mitigation for the 64-byte transaction attack. However, v1 is still decorated only with `#[deprecated]` and `#[pause]` — neither of which restricts external NEAR RPC access: [2](#0-1) 

`#[deprecated]` in Rust is a **compile-time lint warning only**. It has zero effect on NEAR runtime dispatch. Any account can call `verify_transaction_inclusion` directly via RPC, receiving a full SPV result with no coinbase proof required: [3](#0-2) 

The contract's own documentation acknowledges the attack surface: [4](#0-3) 

### Impact Explanation
An attacker can supply a `tx_id` that is the hash of an internal Merkle tree node (not a real transaction). Because v1 performs no coinbase proof depth check, `compute_root_from_merkle_proof` will reconstruct the correct Merkle root from the internal node, and the function returns `true`. Any downstream NEAR contract that calls `verify_transaction_inclusion` — or that an attacker routes through v1 — will accept a forged transaction inclusion proof. This corrupts the SPV proof result, the core security guarantee of the light client.

### Likelihood Explanation
The entry point is fully public with no role restriction. The 64-byte transaction attack is a well-documented, practical technique. An attacker needs only to craft a valid Merkle path from an internal node to the stored Merkle root of any confirmed block — no privileged access, no key material, no social engineering required.

### Recommendation
Remove the `pub` visibility from `verify_transaction_inclusion` or add `#[private]` to make it callable only by the contract itself (as an internal helper for v2). The function should not be externally dispatchable. If backward compatibility must be preserved, gate v1 behind an access-control role so unprivileged callers cannot reach it.

### Proof of Concept
1. Identify any confirmed mainchain block `B` with known Merkle root `R` and at least two transactions.
2. Compute the hash of the left-child internal node `N` at depth 1 of the Merkle tree.
3. Construct a one-element `merkle_proof` = `[sibling_of_N]` such that `hash(N || sibling_of_N) == R`.
4. Call `verify_transaction_inclusion` directly via NEAR RPC with `tx_id = N`, `tx_block_blockhash = B.hash`, `tx_index = 0`, `merkle_proof = [sibling_of_N]`, `confirmations = 1`.
5. The function returns `true` — a forged inclusion proof for a non-existent transaction — because v1 never validates that `N` corresponds to a real transaction via a coinbase depth anchor.

### Citations

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

**File:** contract/CLAUDE.md (L64-67)
```markdown
`verify_transaction_inclusion(ProofArgs)` — SPV proof: given a tx hash, block hash, and merkle proof, verifies the transaction is in the block by recomputing the merkle root.

**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.

```
