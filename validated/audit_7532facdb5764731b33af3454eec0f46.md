### Title
Publicly Callable `verify_transaction_inclusion` Omits Coinbase Merkle Proof Check, Enabling 64-Byte Transaction Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) remains a live, publicly callable NEAR contract method that performs Merkle proof verification without the coinbase proof validation step that was introduced in `verify_transaction_inclusion_v2` to prevent the 64-byte transaction forgery attack. Any unprivileged NEAR caller — including a consumer contract — can invoke v1 directly and receive a `true` result for a forged transaction inclusion proof.

---

### Finding Description

The contract exposes two transaction-inclusion verification methods:

**v1 — missing the coinbase proof check:** [1](#0-0) 

```rust
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
    // ... confirmations check ...
    require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
    merkle_tools::compute_root_from_merkle_proof(
        args.tx_id,
        usize::try_from(args.tx_index).unwrap(),
        &args.merkle_proof,
    ) == header.block_header.merkle_root   // ← no coinbase proof guard
}
```

**v2 — adds the coinbase proof guard before delegating back to v1:** [2](#0-1) 

The `ProofArgs` struct used by v1 has no `coinbase_tx_id` or `coinbase_merkle_proof` fields at all: [3](#0-2) 

whereas `ProofArgsV2` carries both: [4](#0-3) 

The `#[deprecated]` attribute is a **Rust compile-time warning only**. It does not remove the method from the compiled WASM binary, does not gate it behind any role, and does not prevent any external NEAR account or contract from calling it via RPC. The method is `pub`, carries only the shared `#[pause]` guard (same as v2), and is fully reachable by any unprivileged caller.

The own warning in the v1 doc-comment confirms the broken invariant: [5](#0-4) 

> This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.

---

### Impact Explanation

The 64-byte forgery attack (https://www.bitmex.com/blog/64-Byte-Transactions) works because Bitcoin Merkle tree internal nodes are also 64-byte double-SHA256 digests. An attacker can:

1. Pick any real mainchain block already accepted by the contract.
2. Craft a 64-byte value whose double-SHA256 equals an internal node of that block's Merkle tree.
3. Supply that value as `tx_id` together with a valid Merkle proof for that internal node.
4. Call `verify_transaction_inclusion` (v1) — the function computes the root correctly and returns `true`.

Any consumer contract that calls v1 (e.g., an atomic-swap bridge, a cross-chain asset release contract) will accept this forged proof as a real Bitcoin transaction inclusion, potentially releasing funds or triggering irreversible on-chain state changes for a transaction that never existed.

---

### Likelihood Explanation

The entry path requires no privileged role, no leaked key, and no social engineering. Any NEAR account can call `verify_transaction_inclusion` directly via a signed NEAR transaction or a cross-contract call. Consumer contracts built against an older ABI, or those that explicitly target v1 for gas savings, are permanently exposed. The 64-byte forgery technique is publicly documented and has known tooling.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public ABI entirely, or gate it with a role that no external caller can hold. The method should not remain `pub` in the compiled WASM. All callers must be migrated to `verify_transaction_inclusion_v2`, which enforces the coinbase proof check: [6](#0-5) 

---

### Proof of Concept

**Attacker-controlled call (pseudocode):**

```
// 1. Pick a real mainchain block hash already stored in the contract.
let target_block = <any accepted mainchain block hash>;

// 2. Obtain the block's Merkle tree. Find an internal node N at depth d.
//    Craft a 64-byte blob whose double-SHA256 == N.
let forged_tx_id = craft_64_byte_preimage(internal_node_N);

// 3. Build a valid Merkle proof for internal_node_N at its tree position.
let merkle_proof = proof_for_internal_node(target_block, depth_d, position_p);

// 4. Call the deprecated but still-live v1 method.
let result = contract.verify_transaction_inclusion(ProofArgs {
    tx_id: forged_tx_id,
    tx_block_blockhash: target_block,
    tx_index: position_p,
    merkle_proof,
    confirmations: 1,
});

assert!(result == true);  // passes — no coinbase guard in v1
```

The root cause is the structural parallel to the external report: just as `_unstakeLpTokens` omits the `_claim_rewards` flag and therefore skips a necessary action, `verify_transaction_inclusion` omits the coinbase proof validation step and therefore skips the only guard that prevents internal-node forgery. Both omissions leave a reachable, unprivileged code path that produces a materially incorrect outcome.

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

**File:** btc-types/src/contract_args.rs (L16-24)
```rust
#[near(serializers = [borsh, json])]
#[derive(Clone, Debug)]
pub struct ProofArgs {
    pub tx_id: H256,
    pub tx_block_blockhash: H256,
    pub tx_index: u64,
    pub merkle_proof: Vec<H256>,
    pub confirmations: u64,
}
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
