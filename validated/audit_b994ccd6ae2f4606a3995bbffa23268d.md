### Title
Deprecated `verify_transaction_inclusion()` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The contract exposes a deprecated `verify_transaction_inclusion()` function that omits coinbase Merkle proof validation, leaving it vulnerable to the well-documented 64-byte transaction forgery attack. Any unprivileged NEAR caller can invoke this function directly, bypassing the security fix introduced in `verify_transaction_inclusion_v2()`. This is a direct structural analog to the `safeApprove()` issue: a deprecated function with known unsafe behavior remains reachable by external callers.

---

### Finding Description

The contract explicitly deprecates `verify_transaction_inclusion()` since version 0.5.0 and documents the reason — to mitigate the 64-byte transaction Merkle proof forgery vulnerability described at https://www.bitmex.com/blog/64-Byte-Transactions. [1](#0-0) 

Despite the deprecation, the function carries no access restriction beyond the `#[pause]` gate. It remains a fully public contract method callable by any NEAR account when the contract is not paused. [2](#0-1) 

The safe replacement, `verify_transaction_inclusion_v2()`, adds a mandatory coinbase Merkle proof check before delegating to the deprecated function: [3](#0-2) 

The deprecated function itself performs only a raw Merkle root comparison with no coinbase anchor: [4](#0-3) 

Because the 64-byte attack works by crafting a 64-byte input that is simultaneously a valid serialized internal Merkle tree node and a plausible `tx_id`, an attacker can supply a `tx_id` that is actually an internal node hash. The function will compute a Merkle root that matches the block's stored `merkle_root` and return `true` for a transaction that does not exist in the block. `verify_transaction_inclusion_v2()` prevents this by requiring that the coinbase transaction (always a leaf at index 0) independently validates against the same Merkle root, making it computationally infeasible to simultaneously satisfy both proofs with a forged internal node.

---

### Impact Explanation

**Impact: High**

Any consumer contract or cross-chain protocol (bridge, atomic swap, lending) that calls `verify_transaction_inclusion()` and treats a `true` return as proof of Bitcoin transaction inclusion can be deceived into accepting a fraudulent proof. The attacker can fabricate evidence that an arbitrary Bitcoin transaction was confirmed in a real, valid block, triggering downstream actions (e.g., releasing bridged funds, settling a swap) without any corresponding on-chain Bitcoin event.

The corrupted invariant is: `verify_transaction_inclusion() == true` must imply the `tx_id` is a leaf-level transaction hash committed in the block's Merkle tree. The deprecated function breaks this invariant.

---

### Likelihood Explanation

**Likelihood: High**

- The function is publicly callable with no role or allowlist restriction.
- The 64-byte forgery attack is fully documented, with known construction techniques.
- No privileged access, leaked keys, or social engineering is required — any NEAR account can submit the crafted proof arguments.
- The deprecated function is the *default* name a naive integrator would discover first (it predates v2), increasing the probability of real-world usage.

---

### Recommendation

Remove the public accessibility of `verify_transaction_inclusion()`. The simplest safe fix is to add an unconditional panic at the top of the function body:

```rust
env::panic_str("Deprecated: use verify_transaction_inclusion_v2 to prevent Merkle proof forgery");
```

Alternatively, apply an access-control role gate (e.g., `Role::DAO`) so that only privileged internal callers (as `verify_transaction_inclusion_v2` already uses `#[allow(deprecated)]`) can reach it, while external callers are rejected.

---

### Proof of Concept

1. Attacker identifies a real, confirmed Bitcoin block `B` with known `merkle_root` stored in the contract's `headers_pool`.
2. Attacker constructs a 64-byte value `fake_tx_id` that is a valid internal Merkle tree node of `B` at some tree depth, and a corresponding `merkle_proof` path such that `compute_root_from_merkle_proof(fake_tx_id, index, proof) == B.merkle_root`.
3. Attacker calls `verify_transaction_inclusion({ tx_id: fake_tx_id, tx_block_blockhash: B.hash, tx_index: index, merkle_proof: proof, confirmations: 1 })` directly on the NEAR contract.
4. The function returns `true`.
5. Any consumer contract that called this function to gate a cross-chain action (bridge release, swap settlement) now executes based on a fraudulent Bitcoin transaction proof. [4](#0-3)

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
