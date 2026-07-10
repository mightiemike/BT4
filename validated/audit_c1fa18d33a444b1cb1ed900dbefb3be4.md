### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Merkle Proof Check — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a live, publicly callable NEAR contract method despite being deprecated. The v2 replacement adds a coinbase Merkle proof check specifically to block the 64-byte transaction Merkle forgery attack. Because v1 remains reachable by any unprivileged NEAR caller, the coinbase proof guard is entirely bypassable, and the contract can be made to return `true` for a forged transaction inclusion proof.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced to close the 64-byte transaction Merkle forgery vulnerability documented at https://www.bitmex.com/blog/64-Byte-Transactions. It does so by first verifying a coinbase Merkle proof before delegating to v1: [1](#0-0) 

The coinbase check anchors the Merkle tree structure: because the coinbase transaction is always at index 0, a valid coinbase proof proves that the tree's leaf layer contains real transactions, not internal nodes masquerading as leaves.

However, `verify_transaction_inclusion` (v1) is still decorated with `#[pause]` and exposed as a public NEAR method: [2](#0-1) 

Rust's `#[deprecated]` attribute emits a compiler warning only. It does not restrict runtime access. Any NEAR account can call `verify_transaction_inclusion` directly, receiving a proof result that was never subjected to the coinbase check. The contract's own docstring acknowledges the gap: [3](#0-2) 

The structural parallel to the reported Cosmos issue is exact: in Cosmos, `AnteHandle` skipped fee checks when `!ctx.IsCheckTx()`, leaving a second execution path unguarded. Here, the coinbase guard exists only in v2; v1 is the unguarded second path that remains reachable.

---

### Impact Explanation

The 64-byte attack: a Bitcoin Merkle internal node is the double-SHA256 of two concatenated 32-byte child hashes (64 bytes). An attacker can craft a payload that is exactly 64 bytes and whose double-SHA256 equals a known internal node hash `N` at depth `d` in a real block's Merkle tree. Presenting this payload as `tx_id = N` with a Merkle proof rooted at depth `d` will satisfy `compute_root_from_merkle_proof(...) == header.block_header.merkle_root`: [4](#0-3) 

`verify_transaction_inclusion` returns `true` for a transaction that does not exist on Bitcoin. Any downstream NEAR contract that calls v1 to gate an action (e.g., minting a wrapped asset, releasing a cross-chain payment) will execute that action on a forged proof. The corrupted value is the **proof result** returned to the caller.

---

### Likelihood Explanation

- The entry point is fully unprivileged: any NEAR account can call `verify_transaction_inclusion`.
- The 64-byte forgery technique is publicly documented and has known tooling.
- The contract's own deprecation notice signals that integrators are expected to migrate, but the old method is still live and will be called by any integration that has not yet updated.
- No special chain state is required; any block already stored in `headers_pool` and on the main chain can be targeted.

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or replace its body with an unconditional `env::panic_str` that directs callers to `verify_transaction_inclusion_v2`. If a grace-period is needed, the function body should at minimum perform the coinbase proof check before proceeding, making v1 and v2 equivalent in security posture.

---

### Proof of Concept

1. Identify any block `B` already accepted on the contract's main chain (present in `mainchain_header_to_height`).
2. Obtain `B`'s full Merkle tree. Pick any internal node `N` at tree depth `d` (position `p` in the leaf layer of that depth).
3. Craft a 64-byte blob whose double-SHA256 equals `N`. This blob is the forged `tx_id`.
4. Build a standard Merkle proof from depth `d` up to the root; this proof is valid because `N` is a genuine internal node.
5. Call:
   ```
   verify_transaction_inclusion({
     tx_id: <forged_tx_id>,
     tx_block_blockhash: <B>,
     tx_index: <p>,
     merkle_proof: <proof_from_step_4>,
     confirmations: 1
   })
   ```
6. The call returns `true`. No coinbase proof was required. The contract has attested that a non-existent Bitcoin transaction is confirmed in block `B`. [5](#0-4) [6](#0-5)

### Citations

**File:** contract/src/lib.rs (L276-281)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
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

**File:** merkle-tools/src/lib.rs (L33-52)
```rust
#[must_use]
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
