### Title
`verify_transaction_inclusion` Rejects Valid Empty Merkle Proofs for Single-Transaction Blocks — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` (and by extension `verify_transaction_inclusion_v2`) contains an overly strict guard that unconditionally rejects any call where `merkle_proof` is empty. However, an empty Merkle proof is mathematically correct and sufficient when the block contains exactly one transaction: the Merkle root equals the transaction hash directly, and `compute_root_from_merkle_proof` already handles this case correctly by returning the input hash unchanged when the proof slice is empty. The guard fires before the computation is attempted, permanently blocking valid SPV proofs for single-transaction blocks.

### Finding Description

In `contract/src/lib.rs`, `verify_transaction_inclusion` enforces:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

This fires before the Merkle root computation at lines 318–322:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

`compute_root_from_merkle_proof` in `merkle-tools/src/lib.rs` iterates over the proof slice. When the slice is empty the loop body never executes and the function returns `transaction_hash` unchanged:

```rust
for proof_hash in merkle_proof {
    // ...
}
current_hash   // returned as-is when merkle_proof is empty
``` [3](#0-2) 

For a block containing exactly one transaction, the Bitcoin protocol defines the Merkle root as that transaction's hash (no sibling hashing is needed). Therefore `compute_root_from_merkle_proof(tx_id, 0, &[])` correctly returns `tx_id`, which equals `header.block_header.merkle_root`. The computation is sound; only the guard is wrong.

`verify_transaction_inclusion_v2` delegates to `verify_transaction_inclusion` after its own coinbase-proof check:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [4](#0-3) 

For a single-transaction block both `merkle_proof` and `coinbase_merkle_proof` are empty; the length-equality guard (`merkle_proof.len() == coinbase_merkle_proof.len()`, i.e. `0 == 0`) passes, the coinbase-root check passes (empty proof returns `coinbase_tx_id` which equals `merkle_root`), and then the call falls through to `verify_transaction_inclusion` where it panics on the empty-proof guard. Both public entry points are therefore broken for this input class. [5](#0-4) 

### Impact Explanation

Any downstream NEAR contract or off-chain consumer that calls `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` to confirm a Bitcoin transaction that happens to be the sole transaction in its block will receive a contract panic instead of a boolean result. The canonical-chain state is not corrupted, but the verification API returns an incorrect outcome (panic/false instead of true) for a provably valid inclusion proof. Applications that gate asset releases, bridge operations, or other state transitions on this API will permanently fail to process such transactions.

### Likelihood Explanation

Single-transaction blocks occur on Bitcoin testnet and regtest regularly, and occurred on mainnet in Bitcoin's early history (blocks 1–~170 are single-transaction blocks). The contract stores both `Network::Mainnet` and `Network::Testnet` configurations. Any relayer that initialises the contract from an early mainnet height or from testnet, and any downstream consumer that attempts to prove coinbase inclusion in those blocks, will trigger the bug. The call requires no privileged role — `verify_transaction_inclusion_v2` is a public, un-gated (only `#[pause]`) method callable by any NEAR account. [6](#0-5) 

### Recommendation

Remove the blanket empty-proof guard and instead allow `compute_root_from_merkle_proof` to run. Add a targeted check only when the proof is non-empty and the index is out of range, or document that an empty proof is valid for index 0:

```rust
// Remove this line:
// require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

// The computation already handles the empty-proof case correctly:
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

If a non-empty proof is still desired as a protocol invariant, add a separate check that `tx_index == 0` when the proof is empty, to prevent callers from claiming any non-zero index with an empty proof.

### Proof of Concept

1. Initialise the contract with a genesis block that is a single-transaction block (e.g., Bitcoin mainnet block 0 or any early block).
2. Call `verify_transaction_inclusion` with:
   - `tx_id` = the coinbase transaction hash of that block
   - `tx_block_blockhash` = the block hash
   - `tx_index` = 0
   - `merkle_proof` = `[]` (empty — correct for a single-transaction block)
   - `confirmations` = 1
3. The contract panics with `"Merkle proof is empty"` despite the proof being mathematically valid and the transaction genuinely included in the block.
4. Calling `compute_root_from_merkle_proof(tx_id, 0, &[])` directly returns `tx_id`, which equals `merkle_root`, confirming the proof is correct and only the guard is at fault. [7](#0-6)

### Citations

**File:** contract/src/lib.rs (L287-315)
```rust
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
```

**File:** contract/src/lib.rs (L318-322)
```rust
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
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
