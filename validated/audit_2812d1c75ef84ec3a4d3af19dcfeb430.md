### Title
Valid SPV Inclusion Proof Rejected for Single-Transaction Blocks — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` unconditionally panic on an empty `merkle_proof` via a hard `require!` guard. For Bitcoin blocks containing exactly one transaction, the mathematically correct merkle proof **is** an empty vector — the merkle root equals the transaction hash directly. The guard fires before the merkle computation, causing the contract to panic and reject provably valid SPV proofs for single-transaction blocks.

---

### Finding Description

In Bitcoin's merkle tree, a block with a single transaction has `merkle_root == txid`. The correct proof for that transaction is an empty list; no sibling hashes are needed. `compute_root_from_merkle_proof` already handles this correctly: called with an empty proof it returns the input hash unchanged.

Despite this, `verify_transaction_inclusion` enforces:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

This guard fires **before** the merkle computation, so any empty proof — including the only valid proof for a single-tx block — causes a panic.

`verify_transaction_inclusion_v2` (the current recommended production function) inherits the flaw because it delegates to `verify_transaction_inclusion` after its own coinbase-proof check:

```rust
#[allow(deprecated)]
self.verify_transaction_inclusion(args.into())
``` [2](#0-1) 

For a single-tx block the coinbase-proof step in `verify_transaction_inclusion_v2` passes correctly — `compute_root_from_merkle_proof(coinbase_tx_id, 0, &[])` returns `coinbase_tx_id`, which equals `merkle_root` — but the subsequent call to `verify_transaction_inclusion` panics on the empty `merkle_proof`. [3](#0-2) 

The merkle computation itself has no such restriction:

```rust
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 {
    let mut current_hash = transaction_hash;
    // empty proof → loop body never executes → returns transaction_hash unchanged
    for proof_hash in merkle_proof { ... }
    current_hash
}
``` [4](#0-3) 

The guard is therefore both redundant for valid multi-tx inputs and actively harmful for the single-tx edge case.

---

### Impact Explanation

Any NEAR caller or downstream contract invoking `verify_transaction_inclusion_v2` with a valid SPV proof for a transaction in a single-transaction Bitcoin block receives a panic/revert instead of `true`. Cross-chain use cases that gate actions on Bitcoin transaction inclusion (e.g., releasing assets after a confirmed Bitcoin payment) cannot function for single-tx blocks. The broken invariant is: *the contract must return `true` for any proof that correctly reconstructs the stored `merkle_root`*; the guard violates this for the empty-proof case.

---

### Likelihood Explanation

Single-transaction Bitcoin blocks are uncommon but real: they appear throughout early Bitcoin history and during low-fee periods. Any relayer or user attempting to prove inclusion of a coinbase transaction from such a block will trigger this bug. The entry path is fully unprivileged — any NEAR account can call `verify_transaction_inclusion_v2` with attacker-supplied `ProofArgsV2`. [5](#0-4) 

---

### Recommendation

Remove the `require!(!args.merkle_proof.is_empty(), "Merkle proof is empty")` guard, or replace it with a conditional that allows an empty proof only when `tx_id == header.block_header.merkle_root`. Because `compute_root_from_merkle_proof` already handles the empty-proof case correctly, the guard is unnecessary for valid inputs and harmful for the single-tx edge case.

---

### Proof of Concept

1. Submit a Bitcoin block header for a block containing exactly one transaction (coinbase only), so `merkle_root == coinbase_txid`.
2. Call `verify_transaction_inclusion_v2` with:
   - `tx_id = coinbase_txid`, `tx_index = 0`, `merkle_proof = []`
   - `coinbase_tx_id = coinbase_txid`, `coinbase_merkle_proof = []`
   - `confirmations = 0`
3. **Length check** (`merkle_proof.len() == coinbase_merkle_proof.len()`): `0 == 0` → passes.
4. **Coinbase proof check**: `compute_root_from_merkle_proof(coinbase_txid, 0, &[])` = `coinbase_txid` = `merkle_root` → passes.
5. **Delegation**: `verify_transaction_inclusion` is called; `require!(!args.merkle_proof.is_empty(), ...)` fires → contract panics.
6. The mathematically valid proof is rejected with a revert. [6](#0-5)

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
