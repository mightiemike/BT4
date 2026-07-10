### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable On-Chain, Bypassing Coinbase Merkle Proof Forgery Protection — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` is marked `#[deprecated]` in Rust source but remains a fully accessible `pub fn` on the deployed NEAR contract. Any unprivileged NEAR caller can invoke it directly by name, bypassing the coinbase Merkle proof validation that `verify_transaction_inclusion_v2` adds to prevent the 64-byte transaction Merkle proof forgery attack. The `#[deprecated]` attribute is a Rust compiler hint only — it has no effect on on-chain accessibility.

### Finding Description

The contract exposes two SPV proof verification entry points:

- `verify_transaction_inclusion` — deprecated since v0.5.0, performs only a basic Merkle root recomputation with no coinbase proof check.
- `verify_transaction_inclusion_v2` — the secure replacement, which first validates a coinbase Merkle proof to bound the tree depth and prevent internal-node forgery. [1](#0-0) 

The `#[deprecated]` attribute causes the Rust compiler to emit a warning when the function is called from Rust code. It does **not** remove the function from the compiled WASM binary or restrict its on-chain invocability. On NEAR Protocol, any account can call any `pub fn` on a contract by its string name. The deprecated function is therefore fully reachable by any unprivileged caller. [2](#0-1) 

The missing validation is the coinbase proof check that `verify_transaction_inclusion_v2` performs before delegating: [3](#0-2) 

Without this check, the Merkle proof depth is unconstrained. An attacker can supply an internal Merkle tree node hash as `tx_id` and construct a valid-looking proof path that recomputes to the block's `merkle_root`, causing the function to return `true` for a transaction that does not exist. This is the standard Bitcoin Merkle second-preimage / 64-byte transaction forgery attack documented at https://www.bitmex.com/blog/64-Byte-Transactions, and is explicitly acknowledged in the contract's own documentation: [4](#0-3) 

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` to gate a payment, bridge withdrawal, or asset release will receive a forged `true` result for a Bitcoin transaction that was never broadcast or confirmed. The attacker does not need to mine any Bitcoin block — they only need to know the `merkle_root` of an existing confirmed block and craft a proof path from an internal node to that root.

### Likelihood Explanation

The entry point is a standard NEAR view or change call requiring no special role, no staked collateral, and no privileged key. The 64-byte forgery technique is well-documented and tooling exists. Any actor who knows the contract's ABI (which is public) can call `verify_transaction_inclusion` directly, bypassing `verify_transaction_inclusion_v2` entirely.

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI of the deployed contract. In NEAR, this means either:

1. Changing the function visibility so it is no longer exported in the WASM (e.g., make it `pub(crate)` and remove the `#[near]` export), or
2. Adding an explicit `env::panic_str("use verify_transaction_inclusion_v2")` at the top of the function body so any direct on-chain call is rejected at runtime.

A Rust `#[deprecated]` attribute alone is insufficient — it only affects compile-time callers in Rust, not on-chain callers invoking the function by its string name.

### Proof of Concept

1. Attacker identifies a confirmed Bitcoin block already stored in the light client with known `merkle_root`.
2. Attacker selects any internal Merkle tree node hash `N` from that block's transaction tree.
3. Attacker constructs `merkle_proof` such that `compute_root_from_merkle_proof(N, index, proof) == merkle_root`.
4. Attacker calls `verify_transaction_inclusion` directly on the NEAR contract with `tx_id = N`, `tx_block_blockhash = <that block>`, `confirmations = 0`.
5. The function at [5](#0-4)  returns `true`.
6. Any downstream contract that trusted this result releases funds for a Bitcoin transaction that never existed.

### Citations

**File:** contract/src/lib.rs (L283-288)
```rust
    #[deprecated(
        since = "0.5.0",
        note = "Use `verify_transaction_inclusion_v2` instead."
    )]
    #[pause]
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
```

**File:** contract/src/lib.rs (L317-323)
```rust
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

**File:** contract/CLAUDE.md (L66-67)
```markdown
**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.

```
