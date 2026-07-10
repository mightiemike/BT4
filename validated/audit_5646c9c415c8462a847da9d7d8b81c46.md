### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable Without Coinbase Merkle Proof Validation, Enabling 64-Byte Transaction Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The contract exposes two transaction inclusion verification functions. The newer `verify_transaction_inclusion_v2` adds a coinbase Merkle proof check specifically to mitigate the well-known 64-byte transaction Merkle proof forgery attack. The older `verify_transaction_inclusion` is marked `#[deprecated]` but remains a live, callable public method on the NEAR contract. Any unprivileged NEAR caller or downstream recipient contract that invokes the deprecated path bypasses the coinbase proof guard entirely, allowing a forged transaction inclusion proof to be accepted as valid.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to close a known gap: it first validates that the coinbase transaction (at index 0) anchors the Merkle root, then delegates to the v1 path. This two-step check prevents an attacker from supplying a 64-byte internal Merkle tree node as a `tx_id` and having it accepted as a real transaction hash. [1](#0-0) 

The v1 function, however, performs no such coinbase anchor check. It only verifies that the supplied `tx_id` and `merkle_proof` reconstruct the stored `merkle_root`: [2](#0-1) 

The contract's own docstring for v1 explicitly acknowledges the gap:

> *"This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."* [3](#0-2) 

In Rust, `#[deprecated]` emits a compiler warning only. It does not remove the function from the compiled WASM binary or prevent external NEAR transactions from invoking it. The function carries `#[pause]` but is otherwise unrestricted — any NEAR account can call it when the contract is unpaused. [4](#0-3) 

The `compute_root_from_merkle_proof` function used by v1 is a pure positional hash accumulator with no awareness of whether the leaf is a real transaction or an internal node: [5](#0-4) 

---

### Impact Explanation

A downstream NEAR contract or off-chain application that calls `verify_transaction_inclusion` (v1) to gate a security-sensitive action — such as releasing bridged funds, minting wrapped tokens, or authorizing a cross-chain message — can be deceived into accepting a proof for a Bitcoin transaction that does not exist. The attacker supplies a 64-byte internal Merkle node hash as `tx_id` along with a valid sibling path; the function returns `true`, and the downstream action executes against a fabricated transaction. This corrupts the canonical proof result that the light client is supposed to guarantee.

---

### Likelihood Explanation

The 64-byte transaction Merkle forgery is a documented, publicly known Bitcoin attack vector (referenced in the contract's own deprecation notice and the Bitmex blog post cited in the v2 docstring). The v1 function is reachable by any unprivileged NEAR account without any role check or staking requirement. Any integrator that has not migrated to v2 — or any attacker who deliberately targets the v1 endpoint — can exploit this path. The entry point is a standard NEAR cross-contract call or direct transaction, requiring no privileged access.

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public ABI entirely, or add a hard `env::panic_str` body that unconditionally aborts execution, so that no external caller can reach the unguarded Merkle proof path. Deprecation warnings are invisible to NEAR transaction senders and provide no runtime protection.

---

### Proof of Concept

1. Identify any Bitcoin block stored in the light client's `headers_pool` whose Merkle tree has more than one transaction (so internal nodes exist).
2. Select an internal node `N` at depth `d` and position `p` in the Merkle tree. Compute its hash `H(N)`.
3. Construct a sibling path from `H(N)` up to the Merkle root — this is a valid path because `H(N)` is a real internal node.
4. Call `verify_transaction_inclusion` on the NEAR contract with:
   - `tx_id = H(N)` (the internal node hash, not a real txid)
   - `tx_block_blockhash` = the target block hash
   - `tx_index` = `p` (the position of the internal node at depth `d`)
   - `merkle_proof` = the sibling hashes from depth `d` up to the root
   - `confirmations` = any value ≤ stored depth
5. The function computes `compute_root_from_merkle_proof(H(N), p, siblings)` which equals the stored `merkle_root`, and returns `true`.
6. Any downstream contract gating on this `true` result executes its action for a Bitcoin transaction that was never broadcast or confirmed. [6](#0-5) [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L276-282)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
    /// Multiple cases
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
