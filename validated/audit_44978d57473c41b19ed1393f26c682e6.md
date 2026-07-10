### Title
`verify_transaction_inclusion` (v1) Bypasses Coinbase Merkle Proof Validation Enforced by v2 — (File: `contract/src/lib.rs`)

---

### Summary

The deprecated `verify_transaction_inclusion` function remains a live, publicly callable NEAR entry point. It omits the coinbase Merkle proof check that `verify_transaction_inclusion_v2` enforces to prevent the 64-byte transaction forgery attack. Any unprivileged NEAR caller can invoke v1 directly, bypassing the only guard that prevents forged SPV proofs.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability (documented at https://www.bitmex.com/blog/64-Byte-Transactions). It does so by requiring a coinbase Merkle proof of equal length, verifying it against the block's Merkle root, and only then delegating to v1: [1](#0-0) 

The coinbase proof check is the entire security addition: [2](#0-1) 

However, v1 is still decorated only with `#[deprecated]` and `#[pause]` — not `#[private]`. In Rust, `#[deprecated]` is a compile-time lint warning; it does not restrict runtime invocability. On NEAR, any account can call any `pub` method by name. v1 therefore remains a fully reachable public entry point: [3](#0-2) 

v1 performs confirmations checks and a raw Merkle root comparison, but **never validates a coinbase proof**: [4](#0-3) 

The contract's own warning acknowledges the gap: [5](#0-4) 

---

### Impact Explanation

The 64-byte forgery attack allows an adversary to craft a 64-byte blob that is simultaneously a valid serialized internal Merkle tree node and a plausible "transaction ID." By supplying this blob as `tx_id` with a carefully chosen `merkle_proof`, the attacker can make `compute_root_from_merkle_proof` return the real block's Merkle root for a transaction that was never included in that block. v1 will return `true`.

Any bridge, escrow, or downstream NEAR contract that calls `verify_transaction_inclusion` (v1) — or that an attacker tricks into calling it — will accept a forged proof of Bitcoin transaction inclusion. This breaks the core security invariant of the light client: that a `true` result guarantees the transaction is on the canonical Bitcoin chain.

---

### Likelihood Explanation

The entry point is unconditionally reachable by any NEAR account with no staking, role, or deposit requirement beyond gas. The 64-byte forgery technique is publicly documented and has known tooling. The only precondition is that a downstream consumer uses v1 (which is still the default in any integration written before v2 was introduced, and is still callable today).

---

### Recommendation

Mark `verify_transaction_inclusion` with `#[private]` so it is only callable by the contract itself (i.e., from within `verify_transaction_inclusion_v2`). Alternatively, remove the public `#[near]` exposure entirely and keep only the internal delegation path. The `#[deprecated]` attribute alone provides no runtime protection on NEAR.

---

### Proof of Concept

**Attacker-controlled call path:**

1. Attacker identifies a real block on the canonical chain with known Merkle root `R`.
2. Attacker constructs a 64-byte blob `F` and a `merkle_proof` path such that `compute_root_from_merkle_proof(F, index, proof) == R`. (Standard 64-byte forgery construction.)
3. Attacker calls `verify_transaction_inclusion` directly on NEAR with `tx_id = F`, `tx_block_blockhash = <real block hash>`, `merkle_proof = <crafted path>`, `confirmations = 1`.
4. v1 checks confirmations ✓, checks block is on mainchain ✓, checks proof is non-empty ✓, computes root → matches `R` → **returns `true`**.
5. The same call to `verify_transaction_inclusion_v2` would **panic** at the coinbase proof check because no valid coinbase proof exists for the forged `F`.

**Structural parallel to the Ammplify M-2 bug:**

| Ammplify | BTC Light Client |
|---|---|
| `removeMaker` applies JIT penalty | `verify_transaction_inclusion_v2` applies coinbase proof check |
| `collectFees` skips JIT penalty | `verify_transaction_inclusion` (v1) skips coinbase proof check |
| Both paths publicly callable | Both paths publicly callable |
| Attacker calls `collectFees` to bypass penalty | Attacker calls v1 to bypass forgery protection | [6](#0-5) [7](#0-6)

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

**File:** contract/src/lib.rs (L346-369)
```rust
    #[pause]
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
