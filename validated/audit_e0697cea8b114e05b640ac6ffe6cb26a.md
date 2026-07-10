### Title
Publicly Callable Deprecated Verification Bypasses Coinbase-Proof Guard — (`contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` remains a fully public, callable entry point despite being deprecated. `verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle-proof forgery vulnerability by requiring a coinbase Merkle proof of equal depth. Because the old function is still reachable by any unprivileged NEAR caller, the security upgrade is optional rather than enforced, and any downstream contract that calls the old path receives a forged-proof-accepting result.

### Finding Description
The contract exposes two verification entry points with different security guarantees:

`verify_transaction_inclusion` performs SPV proof verification through a sequence of scattered, independent `require` guards — confirmations bound, mainchain membership, non-empty proof — but contains no coinbase-proof check. [1](#0-0) 

`verify_transaction_inclusion_v2` was added to close the 64-byte transaction forgery path by first validating a coinbase Merkle proof of the same depth, then delegating to the old function. [2](#0-1) 

The `#[deprecated]` attribute on `verify_transaction_inclusion` is a Rust compiler hint only; it does not prevent the function from being called on-chain. Any NEAR account — including an adversarial one — can invoke `verify_transaction_inclusion` directly and receive a `true` result for a crafted 64-byte internal-node hash that `verify_transaction_inclusion_v2` would reject.

The contract's own documentation acknowledges the attack surface: [3](#0-2) 

The system therefore has two implicit verification "phases" with no enforced transition between them. The scattered `require` statements in the old function do not include the coinbase guard, and there is no single state-checking function or modifier that forces callers through the secure path — the exact structural problem the external report identifies.

### Impact Explanation
A recipient contract that calls `verify_transaction_inclusion` (or is tricked into doing so) can be made to accept a forged transaction-inclusion proof. An attacker constructs a 64-byte value that is a valid internal Merkle-tree node, submits it as `tx_id`, and the function returns `true`. Any protocol logic gated on this result — e.g., releasing funds, minting tokens, or updating cross-chain state — is compromised.

### Likelihood Explanation
The function is `pub`, requires no role, and is reachable by any NEAR account. The 64-byte forgery technique is well-documented (BitMEX research, CVE-2012-2459). A motivated attacker who identifies a downstream contract relying on `verify_transaction_inclusion` has a straightforward, low-cost exploit path.

### Recommendation
Remove the `pub` visibility from `verify_transaction_inclusion` or gate it with the `#[pause]` macro and an explicit access-control check so it cannot be called externally. Alternatively, delete the function body and make `verify_transaction_inclusion_v2` the sole public entry point, inlining the coinbase-proof check. Following the finite-state-machine pattern suggested by the external report, a single `verify_inclusion` function should enforce all required checks atomically rather than splitting them across two independently callable functions.

### Proof of Concept
1. Deploy the contract (Bitcoin feature flag).
2. Initialize with a valid genesis chain.
3. Submit a real block header so `mainchain_header_to_height` contains its hash.
4. Construct a 64-byte value `fake_tx_id` that equals an internal Merkle-tree node of that block.
5. Build a Merkle proof of depth `d` that leads from `fake_tx_id` to the block's `merkle_root`.
6. Call `verify_transaction_inclusion` with `tx_id = fake_tx_id`, `tx_block_blockhash = <real block hash>`, `tx_index = <crafted index>`, `merkle_proof = <crafted proof>`, `confirmations = 1`.
7. The function returns `true` because `compute_root_from_merkle_proof(fake_tx_id, index, proof) == header.block_header.merkle_root`. [4](#0-3) 
8. The same call to `verify_transaction_inclusion_v2` would fail at the coinbase-proof length check or the coinbase root comparison, rejecting the forgery. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L288-323)
```rust
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

**File:** contract/CLAUDE.md (L66-67)
```markdown
**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.

```
