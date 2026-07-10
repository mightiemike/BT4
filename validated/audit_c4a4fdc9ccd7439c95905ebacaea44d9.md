### Title
`verify_transaction_inclusion_v2` Panics on Valid Empty Merkle Proof for Single-Transaction Blocks — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` delegates to the deprecated `verify_transaction_inclusion` (v1) after validating the coinbase proof, but does not guard against the case where `merkle_proof` is legitimately empty. The v1 function contains an unconditional `require!(!args.merkle_proof.is_empty())` guard that panics for any empty proof, including valid ones for single-transaction blocks. This causes `verify_transaction_inclusion_v2` to permanently return a panic — a corrupted proof result — for a reachable and valid class of inputs.

---

### Finding Description

`verify_transaction_inclusion_v2` performs two steps before returning a result:

1. It validates the coinbase merkle proof against the block's `merkle_root`.
2. It calls `verify_transaction_inclusion` (v1) via `self.verify_transaction_inclusion(args.into())`. [1](#0-0) 

The v1 function contains this guard: [2](#0-1) 

For a block containing only one transaction (the coinbase), the Bitcoin Merkle tree has a single leaf. The `merkle_root` equals the coinbase tx hash directly, and the Merkle proof for that transaction is legitimately **empty** — no sibling hashes are needed. This is a valid, real-world state on all supported chains.

The call chain for this input:

- `merkle_proof.len() == coinbase_merkle_proof.len()` → `0 == 0` → passes.
- `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` → returns `coinbase_tx_id` unchanged.
- `coinbase_tx_id == header.block_header.merkle_root` → passes (valid for single-tx block).
- `self.verify_transaction_inclusion(args.into())` is called with `merkle_proof = []`.
- v1 hits `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` → **panics**. [3](#0-2) 

The `From<ProofArgsV2> for ProofArgs` conversion passes `merkle_proof` through unchanged, so the empty slice reaches v1 unmodified. [4](#0-3) 

The root cause is the missing guard in `verify_transaction_inclusion_v2` before delegating to v1: it never checks whether `merkle_proof.is_empty()` and short-circuits to a direct root comparison, which is the correct and sufficient check for single-transaction blocks.

---

### Impact Explanation

The proof result is corrupted: a valid inclusion proof for a transaction in a single-transaction block produces a panic instead of `true`. Any consumer contract or NEAR caller relying on `verify_transaction_inclusion_v2` to verify such a transaction — e.g., for an atomic swap release, a bridged asset unlock, or a cross-chain state proof — will always receive a panic and can never succeed. The verification path is permanently broken for this input class, with no workaround except falling back to the deprecated v1 function, which reintroduces the 64-byte transaction Merkle forgery vulnerability that v2 was designed to close.

---

### Likelihood Explanation

Single-transaction blocks (coinbase-only) exist on Bitcoin, Litecoin, Dogecoin, and Zcash mainnet and testnet. Any unprivileged NEAR account can call `verify_transaction_inclusion_v2` with a valid proof for such a block. No special role, key, or privileged access is required. The trigger is a normal, well-formed call with a legitimately empty `merkle_proof`.

---

### Recommendation

Add an early-exit guard in `verify_transaction_inclusion_v2` before calling v1. If `merkle_proof` is empty, directly compare `tx_id` to `header.block_header.merkle_root` and return the result:

```rust
// After the coinbase proof check:
if args.merkle_proof.is_empty() {
    return args.tx_id == header.block_header.merkle_root;
}
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
```

Alternatively, remove the `require!(!args.merkle_proof.is_empty())` guard from v1 entirely, since `compute_root_from_merkle_proof` already handles the empty case correctly (it returns the transaction hash unchanged, which is then compared to the merkle root).

---

### Proof of Concept

1. A Bitcoin block at some height contains only the coinbase transaction. Its `merkle_root` equals `coinbase_tx_hash`.
2. The relayer submits this block header via `submit_blocks`; it is stored in `headers_pool`.
3. A consumer calls `verify_transaction_inclusion_v2` with:
   - `tx_id = coinbase_tx_hash`
   - `tx_block_blockhash = block_hash`
   - `tx_index = 0`
   - `merkle_proof = []`
   - `coinbase_tx_id = coinbase_tx_hash`
   - `coinbase_merkle_proof = []`
   - `confirmations = 1`
4. The length check passes (`0 == 0`).
5. The coinbase proof check passes (`coinbase_tx_hash == merkle_root`).
6. `verify_transaction_inclusion` is called with `merkle_proof = []`.
7. Line 315 fires: `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` → **transaction panics**.
8. The consumer contract receives a panic instead of `true`, and the cross-chain operation fails permanently for this block. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L313-323)
```rust
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

**File:** btc-types/src/contract_args.rs (L38-47)
```rust
impl From<ProofArgsV2> for ProofArgs {
    fn from(args: ProofArgsV2) -> Self {
        Self {
            tx_id: args.tx_id,
            tx_block_blockhash: args.tx_block_blockhash,
            tx_index: args.tx_index,
            merkle_proof: args.merkle_proof,
            confirmations: args.confirmations,
        }
    }
```
