### Title
Deprecated `verify_transaction_inclusion` Remains a Live Public Entry Point, Enabling 64-Byte Transaction Merkle Proof Forgery — (File: `contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` is marked `#[deprecated]` in Rust but remains a fully callable public NEAR entry point. It lacks the coinbase Merkle proof validation that `verify_transaction_inclusion_v2` was introduced to provide. Any unprivileged NEAR caller can invoke the deprecated function directly, bypassing the forgery mitigation and obtaining a `true` SPV verification result for a transaction that does not exist in the claimed block.

### Finding Description
The contract exposes two SPV verification functions. The newer one, `verify_transaction_inclusion_v2`, was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability (documented at https://www.bitmex.com/blog/64-Byte-Transactions). It does so by requiring a coinbase Merkle proof that anchors the tree to a known-valid leaf before checking the target transaction. [1](#0-0) 

The older function, `verify_transaction_inclusion`, carries a Rust `#[deprecated]` attribute. In Rust, `#[deprecated]` is a **compiler-level lint only** — it emits a warning to callers at compile time but does not remove the function from the compiled WASM binary or restrict its runtime invocability. The function remains `pub`, is decorated with `#[pause]` (callable when the contract is not paused), and is exported as a NEAR contract method. [2](#0-1) 

The deprecated function's only Merkle-related guard is:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [3](#0-2) 

It then directly computes the Merkle root from the attacker-supplied `tx_id` and `merkle_proof` and compares it to the stored `merkle_root`: [4](#0-3) 

There is no coinbase proof step. The 64-byte attack works as follows: Bitcoin's Merkle tree hashes pairs of 32-byte child hashes to produce a 32-byte parent. A 64-byte blob can be interpreted either as two 32-byte child hashes (an internal node) or as a raw transaction. An attacker crafts a 64-byte "transaction" whose double-SHA256 hash, when placed at a chosen index in a crafted `merkle_proof`, produces the real `merkle_root` of a legitimately confirmed block. `verify_transaction_inclusion` accepts this proof and returns `true`, asserting that the forged "transaction" is confirmed in that block.

`verify_transaction_inclusion_v2` closes this by first verifying that the coinbase transaction (index 0, always a real transaction) hashes through the same proof path to the same `merkle_root`, making it computationally infeasible to forge a sibling path. [5](#0-4) 

### Impact Explanation
Any downstream NEAR contract or off-chain system that calls `verify_transaction_inclusion` to gate a cross-chain action (e.g., releasing bridged assets, crediting a deposit, unlocking collateral) can be deceived into accepting a forged Bitcoin transaction proof. The attacker receives a `true` return value for a transaction that was never broadcast or confirmed on Bitcoin. This is a proof-verification forgery with direct financial impact on any protocol consuming the result.

### Likelihood Explanation
The entry point is public and requires no privileged role. The attacker only needs to: (1) identify a legitimately confirmed Bitcoin block already stored in the contract, (2) craft a 64-byte payload and Merkle path that hashes to that block's `merkle_root`, and (3) call `verify_transaction_inclusion` with the forged arguments. The 64-byte forgery technique is well-documented and tooling exists. The deprecated function is not hidden — it appears in the contract ABI.

### Recommendation
Remove `verify_transaction_inclusion` from the compiled contract entirely, or gate it with an explicit `env::panic_str` at the top of the function body so it reverts unconditionally at runtime. A Rust `#[deprecated]` attribute is insufficient to prevent on-chain invocation. All callers must be migrated to `verify_transaction_inclusion_v2`.

### Proof of Concept

1. Identify a block hash `B` stored in the contract's `mainchain_header_to_height` map (any confirmed mainchain block).
2. Retrieve `B`'s `merkle_root` from `headers_pool`.
3. Construct a 64-byte blob `fake_tx` and a single-element `merkle_proof = [sibling]` such that `SHA256d(SHA256d(fake_tx) || sibling) == merkle_root` (or the appropriate index-dependent ordering). This is the standard 64-byte Merkle forgery construction.
4. Call (NEAR RPC):
   ```json
   {
     "method": "verify_transaction_inclusion",
     "args_borsh": {
       "tx_id": "<fake_tx_hash>",
       "tx_block_blockhash": "<B>",
       "tx_index": 0,
       "merkle_proof": ["<sibling>"],
       "confirmations": 1
     }
   }
   ```
5. The function returns `true`. No Bitcoin transaction with `fake_tx_hash` exists. [2](#0-1)

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
