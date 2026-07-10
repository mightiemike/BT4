### Title
`verify_transaction_inclusion_v2` Becomes Silently Broken When `verify_transaction_inclusion` Is Independently Paused — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` delegates its final proof check to `verify_transaction_inclusion` via a direct internal Rust call. Because `near_plugins` `#[pause]` inserts the pause guard **into the function body** (not only into the NEAR dispatch wrapper), pausing the deprecated v1 function also silently breaks v2 — even though v2 carries its own independent `#[pause]` attribute and is not paused. Any downstream contract that migrated to v2 to gain the coinbase-proof security fix will have all SPV verification calls revert unexpectedly.

---

### Finding Description

`verify_transaction_inclusion` is marked both `#[deprecated]` and `#[pause]`: [1](#0-0) 

`verify_transaction_inclusion_v2` carries its own independent `#[pause]` attribute and, after performing the coinbase-proof check, delegates to v1 via a direct Rust method call: [2](#0-1) 

`near_plugins` v0.2.0 (tag v0.4.1) injects the pause guard at the **top of the function body**, not only in the NEAR ABI dispatch shim. Consequently, `self.verify_transaction_inclusion(args.into())` at line 368 executes the pause check for the `"verify_transaction_inclusion"` feature regardless of whether the call originates externally or internally. When a `PauseManager` pauses `verify_transaction_inclusion` — a natural operational action to force migration away from the deprecated, Merkle-forgery-vulnerable endpoint — every call to `verify_transaction_inclusion_v2` panics inside the internal delegation, even though v2 itself is not paused.

The two functions are independently pausable features (each uses its own function name as the feature key), so the admin has no reason to expect that pausing v1 affects v2. [3](#0-2) [4](#0-3) 

---

### Impact Explanation

Any NEAR contract that consumes `verify_transaction_inclusion_v2` for cross-chain SPV proof verification (bridges, payment rails, custody systems) will have every verification call revert the moment an admin pauses the deprecated v1 endpoint. The canonical proof-result that downstream contracts depend on — `true` / `false` for transaction inclusion — becomes permanently unavailable without the admin realising v2 is also affected. This can freeze withdrawals, block settlements, or cause loss of funds in protocols that gate asset release on a successful SPV proof.

---

### Likelihood Explanation

The scenario is operationally plausible: a `PauseManager` wanting to deprecate the insecure v1 endpoint (which is already marked `#[deprecated(since = "0.5.0")]` and documented as vulnerable to the 64-byte Merkle forgery) would pause it as a routine migration step. Nothing in the code, comments, or role documentation warns that doing so also disables v2. The `UnrestrictedSubmitBlocks` and `UnrestrictedRunGC` bypass roles exist precisely because the team anticipated cross-function pause interactions for `submit_blocks` and `run_mainchain_gc` — but no equivalent bypass role exists for `verify_transaction_inclusion_v2`, confirming the interaction was not anticipated. [5](#0-4) 

---

### Recommendation

Remove the internal delegation. Inline the shared proof logic into a private helper function (e.g., `verify_merkle_inclusion_inner`) that carries no `#[pause]` attribute, and have both public endpoints call that helper independently:

```rust
// private, no #[pause]
fn verify_merkle_inclusion_inner(&self, args: ProofArgs) -> bool { … }

#[deprecated] #[pause]
pub fn verify_transaction_inclusion(&self, args: ProofArgs) -> bool {
    self.verify_merkle_inclusion_inner(args)
}

#[pause]
pub fn verify_transaction_inclusion_v2(&self, args: ProofArgsV2) -> bool {
    // coinbase check …
    self.verify_merkle_inclusion_inner(args.into())
}
```

This eliminates the cross-function pause dependency while preserving independent pausability for each public endpoint.

---

### Proof of Concept

1. Deploy the contract (Bitcoin feature, `skip_pow_verification = true`).
2. Grant account `alice` the `PauseManager` role.
3. `alice` calls `pa_pause_feature("verify_transaction_inclusion")` — a routine deprecation action.
4. Any unprivileged caller submits a valid `ProofArgsV2` to `verify_transaction_inclusion_v2`.
5. v2 passes its own pause check (not paused), performs the coinbase proof check successfully, then calls `self.verify_transaction_inclusion(args.into())` at line 368.
6. The `near_plugins` pause guard inside `verify_transaction_inclusion` fires for feature `"verify_transaction_inclusion"` and panics.
7. The transaction reverts. The caller receives no inclusion result despite v2 being nominally active. [6](#0-5) [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L40-46)
```rust
pub enum Role {
    /// May pause and unpause features.
    PauseManager,
    /// Allows to use contract API even after contract is paused
    UnrestrictedSubmitBlocks,
    // Allows to use `run_mainchain_gc` API on a paused contract
    UnrestrictedRunGC,
```

**File:** contract/src/lib.rs (L167-168)
```rust
    #[pause]
    #[trusted_relayer]
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
