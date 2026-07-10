### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Enabling 64-Byte Merkle Proof Forgery — (File: `contract/src/lib.rs`)

---

### Summary

The contract exposes two transaction-inclusion verification endpoints. The v2 endpoint (`verify_transaction_inclusion_v2`) adds a coinbase-proof check to mitigate the well-known 64-byte Merkle proof forgery attack. However, the original v1 endpoint (`verify_transaction_inclusion`) is still a live, unpermissioned public NEAR method. Any caller can invoke v1 directly, bypassing the coinbase-proof guard entirely, and obtain a `true` return value for a fabricated transaction inclusion claim.

---

### Finding Description

`verify_transaction_inclusion` (v1) is annotated `#[deprecated]` but Rust's `#[deprecated]` attribute is a compile-time lint only; it does not remove the method from the deployed contract's ABI or restrict who may call it at runtime. The method carries only `#[pause]`, meaning it is live and callable by any NEAR account unless an operator explicitly pauses it. [1](#0-0) 

The v2 endpoint adds a coinbase-proof check before delegating to v1: [2](#0-1) 

The coinbase check enforces that a known leaf node (the coinbase transaction at index 0) has a proof of the same depth as the claimed transaction proof. This prevents an attacker from supplying an internal Merkle-tree node as `tx_id`, because the attacker would also need to supply a valid coinbase proof of matching depth.

When v1 is called directly, that guard is absent. The only checks performed are:

1. `args.confirmations <= gc_threshold`
2. `tx_block_blockhash` is in the mainchain
3. `!args.merkle_proof.is_empty()`
4. `compute_root_from_merkle_proof(tx_id, tx_index, merkle_proof) == header.merkle_root` [3](#0-2) 

Check 4 is satisfied by any internal node of the Merkle tree: if `tx_id = H(left || right)` is an internal node whose sibling path leads to the stored `merkle_root`, the function returns `true` without the node ever corresponding to a real transaction.

---

### Impact Explanation

Any downstream NEAR contract that calls `verify_transaction_inclusion` (v1) to gate an action (e.g., releasing funds, minting tokens, recording a cross-chain event) can be deceived into accepting a fabricated Bitcoin transaction inclusion proof. The attacker does not need to mine a real Bitcoin block or produce a valid 64-byte transaction; they only need to identify an internal Merkle-tree node whose sibling path reconstructs the stored `merkle_root` for a mainchain block already accepted by the light client. The return value `true` is indistinguishable from a legitimate proof.

---

### Likelihood Explanation

The function is publicly callable with no role restriction. The 64-byte Merkle forgery technique is documented, well-understood, and the contract's own code comments acknowledge it explicitly. Any integrator that was written against the pre-v2 API, or that calls v1 for any reason, is immediately exploitable. The attacker needs only a valid mainchain block hash (publicly readable from the contract) and knowledge of that block's Merkle tree structure (publicly available from Bitcoin RPC).

---

### Recommendation

Remove `verify_transaction_inclusion` (v1) from the public ABI entirely, or gate it with an access-control role that prevents unprivileged callers from invoking it. The simplest safe option is to make it `pub(crate)` or `private`, since v2 already calls it internally via `#[allow(deprecated)]`. Alternatively, add a hard `require!(false, "use verify_transaction_inclusion_v2")` guard at the top of v1 to make it permanently non-functional for external callers while preserving the internal call path from v2.

---

### Proof of Concept

1. Identify any mainchain block `B` accepted by the contract (call `get_block_hash_by_height`).
2. Obtain block `B`'s full transaction list from Bitcoin RPC and reconstruct its Merkle tree.
3. Select any internal node `N` at depth `d ≥ 1` and collect its sibling path `[s_1, …, s_d]` to the root.
4. Call `verify_transaction_inclusion` with:
   - `tx_id = N` (the internal node hash, not a real txid)
   - `tx_block_blockhash = B`
   - `tx_index` = the position consistent with the sibling path
   - `merkle_proof = [s_1, …, s_d]`
   - `confirmations = 1`
5. The function computes `compute_root_from_merkle_proof(N, tx_index, [s_1,…,s_d])` which equals `B.merkle_root` by construction, and returns `true`. [4](#0-3) 

No Bitcoin mining, no privileged NEAR role, and no contract state modification is required. The attacker only reads public on-chain data and submits a single NEAR function call.

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
