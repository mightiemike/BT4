### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing 64-Byte Merkle Forgery Protection — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is annotated `#[deprecated]` in Rust but is still compiled into the NEAR contract as a fully public, externally callable method. Any unprivileged NEAR account or downstream recipient contract can invoke it directly, completely bypassing the coinbase Merkle proof check that was introduced in `verify_transaction_inclusion_v2` to prevent the 64-byte transaction Merkle proof forgery attack.

---

### Finding Description

The contract exposes two proof-verification entry points:

- `verify_transaction_inclusion` (v1) — marked `#[deprecated(since = "0.5.0")]`, performs only a standard Merkle root comparison with no coinbase anchor check.
- `verify_transaction_inclusion_v2` — the secure replacement; it first validates a coinbase Merkle proof against the block's Merkle root before delegating to v1.

Rust's `#[deprecated]` attribute is a **compile-time lint only**. It emits a warning to Rust callers at compile time but has zero effect on the compiled WASM binary or the NEAR contract ABI. The v1 function is declared `pub` and carries the `#[pause]` macro, meaning it is exported as a callable contract method identical in accessibility to v2. [1](#0-0) 

Because the NEAR runtime dispatches calls by method name string, any external account can submit a transaction calling `verify_transaction_inclusion` directly, receiving a `true` result without ever supplying a `coinbase_tx_id` or `coinbase_merkle_proof`. [2](#0-1) 

The v2 guard that prevents this is entirely absent in v1: [3](#0-2) 

---

### Impact Explanation

The 64-byte transaction attack (documented at https://www.bitmex.com/blog/64-Byte-Transactions) allows an attacker to craft a 64-byte input that is simultaneously a valid internal Merkle tree node and a plausible transaction hash. Without the coinbase anchor check, `compute_root_from_merkle_proof` can be made to return the correct Merkle root for a fabricated `tx_id` that does not correspond to any real transaction. The v1 function returns `true` for such a forged proof.

Any recipient contract that calls `verify_transaction_inclusion` (v1) — whether by mistake, by following outdated documentation, or because an attacker directs it there — will accept a forged Bitcoin transaction inclusion proof as valid. This corrupts the proof-verification result that the entire SPV system is designed to guarantee. [4](#0-3) 

---

### Likelihood Explanation

The method name `verify_transaction_inclusion` is the natural, unsuffixed name a developer or integrating contract would reach for. The v2 variant requires knowing to append `_v2`. Downstream contracts that integrated before v0.5.0 continue calling v1 without any on-chain enforcement to stop them. An attacker who knows the ABI can also deliberately target v1 to bypass the coinbase check.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public contract interface entirely. If backward compatibility must be preserved temporarily, replace the v1 body with an unconditional `env::panic_str("use verify_transaction_inclusion_v2")` so that any call to the legacy method fails at runtime rather than silently returning a potentially forged result. The `#[deprecated]` attribute alone provides no on-chain protection.

---

### Proof of Concept

1. Identify a real Bitcoin block whose Merkle root is known and stored in the contract.
2. Construct a 64-byte blob `tx_id` that is a valid internal Merkle node such that `compute_root_from_merkle_proof(tx_id, index, proof) == merkle_root` (standard CVE-2017-12842 / BIP-141 technique).
3. Call `verify_transaction_inclusion` (v1) directly on the NEAR contract with this fabricated `tx_id`, a valid `tx_block_blockhash`, and the crafted `merkle_proof`.
4. The function returns `true` — a forged transaction inclusion proof is accepted — because no coinbase anchor check is performed.
5. Calling `verify_transaction_inclusion_v2` with the same inputs (and no valid `coinbase_merkle_proof`) would panic with `"Incorrect coinbase merkle proof"`, confirming v2 blocks the attack while v1 does not. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L277-279)
```rust
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
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
