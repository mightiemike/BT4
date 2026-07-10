### Title
`verify_transaction_inclusion` Omits Coinbase Proof Check Present in `verify_transaction_inclusion_v2`, Enabling 64-Byte Transaction Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The public NEAR contract method `verify_transaction_inclusion` validates Merkle inclusion without performing the coinbase proof check that was introduced in `verify_transaction_inclusion_v2` to mitigate the 64-byte transaction Merkle forgery vulnerability. The function is deprecated but remains callable by any unprivileged NEAR account. An attacker can supply an internal Merkle tree node as `tx_id` with a crafted proof and receive `true`, forging a transaction inclusion result that any consuming DApp would treat as a confirmed Bitcoin transaction.

---

### Finding Description

`verify_transaction_inclusion` (the deprecated v1 path) is still a live, unguarded public NEAR contract method. It validates inclusion by computing the Merkle root from the caller-supplied `tx_id`, `tx_index`, and `merkle_proof`, then comparing the result to the stored block's `merkle_root`: [1](#0-0) 

It performs no coinbase proof check. The coinbase proof check was introduced precisely to close the 64-byte transaction vulnerability (referenced in the doc comment at line 271) and lives exclusively in `verify_transaction_inclusion_v2`: [2](#0-1) 

`verify_transaction_inclusion_v2` also enforces that both proofs have equal length before delegating to the v1 path: [3](#0-2) 

Because `verify_transaction_inclusion` is still exposed as a `pub` method under `#[near] impl BtcLightClient` and is only annotated `#[deprecated]` (a compile-time hint, not a runtime restriction), any NEAR caller can invoke it directly, bypassing the coinbase proof gate entirely. [4](#0-3) 

The 64-byte attack works as follows against `verify_transaction_inclusion`: given a real main-chain block whose Merkle tree is known, any internal node `N` at depth `d` can be used as `tx_id`. The attacker sets `tx_index` to the leaf-equivalent position of `N` and supplies the `d-1` sibling hashes above `N` as `merkle_proof`. `compute_root_from_merkle_proof(N, tx_index, proof)` then reconstructs the correct `merkle_root`, and the function returns `true`. [5](#0-4) 

The coinbase check in v2 defeats this because the coinbase transaction occupies position 0 at leaf depth, and its proof must be length-equal to the target proof. An internal node at depth `d` cannot simultaneously satisfy a valid coinbase proof of the same length for a different leaf position, making the forgery infeasible when both checks are enforced together.

---

### Impact Explanation

Any external NEAR DApp that calls `verify_transaction_inclusion` (rather than the v2 variant) and acts on a `true` result — releasing funds, minting tokens, updating state — can be deceived into treating a non-existent Bitcoin transaction as confirmed. The corrupted value is the boolean proof result returned to the consuming contract; the broken invariant is that `true` must imply a real leaf-level transaction hash, not an internal Merkle node.

---

### Likelihood Explanation

The function is publicly callable by any NEAR account with no role restriction beyond the pausable flag (which is off by default). DApps that integrated before v2 was introduced, or that read the ABI without noticing the deprecation notice, will call v1 directly. The 64-byte attack requires only knowledge of a real main-chain block's Merkle tree, which is public Bitcoin data. No privileged access, key material, or social engineering is needed.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or add the coinbase proof check directly into it so that both entry points enforce the same security invariant. Keeping a callable deprecated method that omits a critical security check creates a permanent forgery surface for any caller that has not migrated.

---

### Proof of Concept

1. Identify any block `B` on the contract's main chain (e.g., via `get_block_hash_by_height`).
2. Obtain `B`'s full Merkle tree from a Bitcoin node. Pick any internal node `N` at depth `d` (e.g., the left child of the root, which is `hash(tx0, tx1)`).
3. Set `tx_id = N`, `tx_index = 0` (or the appropriate even index for `N`'s position), and `merkle_proof = [right_sibling_of_N_at_root_level]` (length 1 for depth-1 node).
4. Call `verify_transaction_inclusion({ tx_id, tx_block_blockhash: B, tx_index, merkle_proof, confirmations: 1 })`.
5. `compute_root_from_merkle_proof(N, 0, [sibling]) == merkle_root` evaluates to `true`; the function returns `true` for a transaction that does not exist. [6](#0-5)

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

**File:** contract/src/lib.rs (L348-351)
```rust
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );
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
