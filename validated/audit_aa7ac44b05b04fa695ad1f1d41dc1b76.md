### Title
Incorrect Guard Rejects Valid SPV Proof for Single-Transaction Blocks — (`contract/src/lib.rs`)

### Summary
`verify_transaction_inclusion` (and by extension `verify_transaction_inclusion_v2`) contains a `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` guard that unconditionally rejects any proof with an empty sibling list. For a Bitcoin block containing exactly one transaction, the Merkle proof is legitimately empty — the transaction hash *is* the Merkle root — yet the guard fires and panics before the comparison is ever evaluated, permanently blocking valid SPV verification for that block.

### Finding Description
In `verify_transaction_inclusion` at line 315:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

`compute_root_from_merkle_proof` handles an empty proof correctly — it simply returns `transaction_hash` unchanged (the loop body never executes):

```rust
for proof_hash in merkle_proof {   // never entered when merkle_proof is empty
    ...
}
current_hash   // == transaction_hash
```

So for a single-transaction block where `merkle_root == tx_id`, passing an empty `merkle_proof` would produce the correct root and the function would return `true`. The `require!` guard fires first and prevents this.

`verify_transaction_inclusion_v2` delegates to the deprecated function after its own coinbase check, so it inherits the same defect. For a single-transaction block, `coinbase_merkle_proof` is also empty, the length-equality check passes (0 == 0), the coinbase root check passes (`coinbase_tx_id == merkle_root`), and then the inner call panics on the empty-proof guard.

### Impact Explanation
Any downstream NEAR contract that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to gate a fund release or state transition for a transaction in a single-transaction Bitcoin block will receive a panic (treated as `false`) instead of `true`. The proof is mathematically valid and the block is on-chain; the rejection is caused solely by the incorrect guard. Funds or protocol actions gated on this verification are permanently inaccessible for that class of block.

### Likelihood Explanation
Single-transaction blocks (coinbase only) are uncommon on mainnet today but are fully valid by the Bitcoin protocol and do occur — especially on testnet, during low-activity periods, or on altcoin chains (Litecoin, Dogecoin) supported by the same contract. Any relayer that submits such a block and any user who then tries to prove the coinbase transaction will trigger the bug deterministically. No privileged access is required; the entry point is the public `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` call.

### Recommendation
Remove the `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` guard. The downstream comparison against `header.block_header.merkle_root` is the correct and sufficient check; it will return `false` for any invalid proof, including a spuriously empty one for a multi-transaction block, while correctly returning `true` for a legitimately empty proof in a single-transaction block.

### Proof of Concept
1. Submit a Bitcoin block header whose `merkle_root` equals a single transaction hash `T` (a coinbase-only block).
2. Call `verify_transaction_inclusion` with `tx_id = T`, `tx_index = 0`, `merkle_proof = []`, `confirmations = 1`.
3. Execution reaches line 315: `require!(!args.merkle_proof.is_empty())` → panics with `"Merkle proof is empty"`.
4. Without the guard, `compute_root_from_merkle_proof(T, 0, &[])` returns `T`, which equals `header.block_header.merkle_root`, and the function returns `true`.

Relevant lines: [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contract/src/lib.rs (L315-323)
```rust
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
