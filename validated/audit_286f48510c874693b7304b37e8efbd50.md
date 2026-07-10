### Title
`verify_transaction_inclusion` Always Panics for Single-Transaction Blocks Due to Unconditional Empty-Proof Guard - (File: contract/src/lib.rs)

### Summary

`verify_transaction_inclusion` (and `verify_transaction_inclusion_v2`, which delegates to it) unconditionally rejects any call where `merkle_proof` is empty. For a Bitcoin block containing exactly one transaction, the Merkle root **is** the transaction hash and no proof elements are needed — the proof is legitimately empty. The guard therefore causes every valid inclusion proof for a single-transaction block to panic, permanently blocking any consumer contract from verifying such transactions.

### Finding Description

`verify_transaction_inclusion` contains the guard:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

This fires before the Merkle computation is attempted. For a block with exactly one transaction, the correct proof is the empty vector: `compute_root_from_merkle_proof(tx_id, 0, &[])` simply returns `tx_id`, which equals `merkle_root` for that block. The guard prevents this correct path from ever being reached.

`verify_transaction_inclusion_v2` does not add its own empty-proof check; it validates the coinbase proof first and then delegates unconditionally to `verify_transaction_inclusion`:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [2](#0-1) 

For a single-tx block, `coinbase_merkle_proof` is also empty. The length-equality check (`merkle_proof.len() == coinbase_merkle_proof.len()`) passes (both are 0). `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` returns `coinbase_tx_id`, which equals `merkle_root`, so the coinbase check passes. Execution then falls into `verify_transaction_inclusion`, which panics on the empty-proof guard. [3](#0-2) 

The underlying Merkle computation in `merkle-tools` is correct for the empty case — the loop body is never entered and the input hash is returned unchanged:

```rust
for proof_hash in merkle_proof {   // never executes when merkle_proof is empty
    ...
}
current_hash   // == transaction_hash == merkle_root for a single-tx block
``` [4](#0-3) 

The guard is therefore incorrect: it rejects a class of inputs that the rest of the code handles correctly.

### Impact Explanation

Any third-party NEAR contract that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to confirm a Bitcoin transaction that happens to be in a single-transaction block will always receive a panic (contract abort) rather than a boolean result. Cross-chain applications — atomic swaps, bridged-asset releases, cross-chain state proofs — that depend on this verification path are permanently broken for this class of valid Bitcoin blocks. The contract cannot be worked around by the caller; there is no alternative API path.

**Impact: High** — legitimate on-chain verification of a valid class of Bitcoin transactions is permanently and unconditionally blocked.

### Likelihood Explanation

Empty Bitcoin blocks (containing only the coinbase transaction) occur regularly on mainnet, particularly during periods of low mempool activity or immediately after a difficulty adjustment. Any relayer that submits such a block header to the light client creates a window where a consumer trying to prove the coinbase transaction (or any transaction in that block) will always fail. The trigger requires no special privileges — any unprivileged NEAR account can call `verify_transaction_inclusion`.

**Likelihood: Medium** — single-tx blocks are a normal, recurring part of the Bitcoin protocol.

### Recommendation

Remove the unconditional empty-proof guard and instead handle the single-transaction case explicitly:

```rust
// If proof is empty, the tx_id must equal the merkle_root directly (single-tx block)
if args.merkle_proof.is_empty() {
    return args.tx_id == header.block_header.merkle_root;
}
```

Alternatively, allow `compute_root_from_merkle_proof` to handle the empty case (it already does so correctly) and remove the `require!` entirely, relying on the final equality check to reject invalid proofs.

### Proof of Concept

1. A Bitcoin block is mined containing only the coinbase transaction. Its `merkle_root` equals `coinbase_tx_id`.
2. The relayer submits the block header via `submit_blocks`; it is accepted and stored.
3. A consumer contract calls `verify_transaction_inclusion_v2` with:
   - `tx_id = coinbase_tx_id`
   - `tx_block_blockhash` = the block's hash
   - `tx_index = 0`
   - `merkle_proof = []`
   - `coinbase_tx_id = coinbase_tx_id`
   - `coinbase_merkle_proof = []`
   - `confirmations = 1`
4. Length check passes (both proofs have length 0). [5](#0-4) 
5. `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` returns `coinbase_tx_id` == `merkle_root` → coinbase check passes. [6](#0-5) 
6. `verify_transaction_inclusion` is called; `require!(!args.merkle_proof.is_empty())` fires → contract panics. [1](#0-0) 
7. The consumer contract receives an abort instead of `true`, and any action gated on this proof is permanently blocked.

### Citations

**File:** contract/src/lib.rs (L315-315)
```rust
        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
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

**File:** merkle-tools/src/lib.rs (L42-51)
```rust
    for proof_hash in merkle_proof {
        if current_position % 2 == 0 {
            current_hash = compute_hash(&current_hash, proof_hash);
        } else {
            current_hash = compute_hash(proof_hash, &current_hash);
        }
        current_position /= 2;
    }

    current_hash
```
