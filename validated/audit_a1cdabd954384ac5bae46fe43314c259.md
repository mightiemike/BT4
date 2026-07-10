### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing Coinbase Proof Guard in v2 — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` (v1) is still a `pub` function callable by any unprivileged NEAR account. The v2 upgrade (`verify_transaction_inclusion_v2`) was introduced specifically to add a coinbase Merkle proof check that mitigates the 64-byte transaction Merkle proof forgery attack. However, because v1 remains publicly reachable, any caller can skip v2 entirely and invoke v1 directly, bypassing the only on-chain guard against proof forgery.

---

### Finding Description

The expected execution flow is:

1. Caller invokes `verify_transaction_inclusion_v2` (validates coinbase Merkle proof)
2. v2 internally calls `verify_transaction_inclusion` (v1) after the guard passes [1](#0-0) 

v2 enforces the coinbase proof check before delegating to v1: [2](#0-1) 

However, v1 is still declared `pub` with no caller restriction: [3](#0-2) 

Any NEAR account can call v1 directly, completely skipping the coinbase proof validation. The `#[deprecated]` attribute is a compiler hint only — it does not restrict runtime access on NEAR.

The contract's own documentation acknowledges the risk explicitly: [4](#0-3) 

---

### Impact Explanation

The 64-byte transaction Merkle proof forgery attack (documented at https://www.bitmex.com/blog/64-Byte-Transactions) allows an attacker to construct a fake 64-byte "transaction" whose hash equals an internal Merkle tree node. A valid Merkle proof path can then be supplied for this internal node, causing `verify_transaction_inclusion` to return `true` for a transaction that was never included in the block.

Any downstream NEAR contract or off-chain system that calls v1 directly — or that an attacker tricks into calling v1 — receives a forged `true` result. This corrupts the trust model of the light client: the canonical proof verification result is wrong, and any asset release, bridge unlock, or state transition gated on that result can be triggered for a non-existent Bitcoin transaction.

---

### Likelihood Explanation

The entry path requires no privileges: any NEAR account can call `verify_transaction_inclusion` with attacker-controlled `ProofArgs`. The 64-byte forgery technique is well-documented and has known tooling. Downstream bridge or DeFi contracts that consume this light client's verification result are the realistic targets. The likelihood is high wherever a consumer contract does not independently enforce use of v2.

---

### Recommendation

Remove the `pub` visibility from `verify_transaction_inclusion` (v1) or restrict it to `pub(crate)` / `private` so it is only reachable as an internal helper called by v2. All external callers must be forced through `verify_transaction_inclusion_v2`.

```rust
// Before (exploitable):
pub fn verify_transaction_inclusion(&self, ...) -> bool { ... }

// After (safe):
pub(crate) fn verify_transaction_inclusion(&self, ...) -> bool { ... }
``` [5](#0-4) 

---

### Proof of Concept

1. Attacker identifies a Bitcoin block `B` with known Merkle root `R` stored in the contract's `headers_pool`.
2. Attacker constructs a 64-byte value `F` = `concat(left_child_hash, right_child_hash)` for some internal Merkle node `N` at depth `d`, such that `hash(F) == N`.
3. Attacker builds a valid Merkle proof path from `N` up to `R`.
4. Attacker calls `verify_transaction_inclusion` directly (bypassing v2) with:
   - `tx_id` = `hash(F)` (the internal node hash)
   - `tx_block_blockhash` = hash of block `B`
   - `merkle_proof` = the path from `N` to `R`
   - `tx_index` = index consistent with the path
5. `compute_root_from_merkle_proof` recomputes `R` correctly from the internal node, and the function returns `true`.
6. Any downstream contract gated on this result treats the forged transaction as confirmed. [6](#0-5)

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

**File:** contract/src/lib.rs (L346-368)
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
```

**File:** contract/CLAUDE.md (L66-66)
```markdown
**Important**: This function is vulnerable to the standard Bitcoin merkle tree second-preimage attack — it may return `true` for an internal node hash rather than a real transaction hash. Block headers do not contain the transaction count, so proof depth cannot be validated on-chain. Callers MUST validate that the `tx_id` corresponds to a valid transaction (e.g., by verifying raw transaction data) before trusting the inclusion proof.
```
