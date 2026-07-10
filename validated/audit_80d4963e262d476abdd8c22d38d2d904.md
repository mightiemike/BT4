### Title
`verify_transaction_inclusion` reverts for valid single-transaction block proofs due to unconditional empty-proof guard — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` unconditionally rejects empty merkle proofs via a hard `require!`. However, for a Bitcoin block containing exactly one transaction (e.g., a coinbase-only block), the merkle proof is legitimately empty — the transaction hash *is* the merkle root. The underlying `compute_root_from_merkle_proof` handles this correctly, but the guard fires first and panics, permanently bricking SPV verification for this class of blocks.

---

### Finding Description

In `verify_transaction_inclusion`, after confirming the block is on the main chain and has enough confirmations, the following unconditional guard is applied:

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
``` [1](#0-0) 

This fires before the actual merkle computation. The computation itself is:

```rust
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
``` [2](#0-1) 

`compute_root_from_merkle_proof` iterates over the proof slice. With an empty slice, the loop body never executes and the function returns `transaction_hash` unchanged:

```rust
for proof_hash in merkle_proof {
    // ...
}
current_hash  // returned as-is when merkle_proof is empty
``` [3](#0-2) 

For a single-transaction block, `merkle_root == tx_id` by Bitcoin protocol definition. An empty proof is therefore cryptographically correct and the comparison `compute_root_from_merkle_proof(tx_id, 0, &[]) == merkle_root` would evaluate to `true`. The unconditional `require!` prevents this from ever being reached.

`verify_transaction_inclusion_v2` is equally affected: it validates the coinbase proof (which also passes with an empty slice for a single-tx block), then delegates to `verify_transaction_inclusion` via `self.verify_transaction_inclusion(args.into())`, which panics on the same guard. [4](#0-3) 

**Analog mapping to the external report:**
- External report: `_unlockTokens(from, value, false)` is called unconditionally even when `from` has zero locked tokens → revert.
- This repo: `require!(!args.merkle_proof.is_empty())` is applied unconditionally even when an empty proof is the correct proof → revert.

In both cases, a sub-check that is only meaningful in a subset of valid states is applied to all states, causing legitimate operations to fail.

---

### Impact Explanation

Any NEAR caller or downstream contract invoking `verify_transaction_inclusion` or `verify_transaction_inclusion_v2` for a transaction in a single-transaction Bitcoin block will receive a panic (`"Merkle proof is empty"`) regardless of proof correctness. The SPV verification result is corrupted from `true` (correct) to a hard revert, permanently blocking this class of proofs. Downstream contracts that gate actions on a `true` result (e.g., cross-chain bridges, payment verification) cannot proceed.

---

### Likelihood Explanation

Single-transaction blocks (coinbase only) are a normal part of the Bitcoin protocol and occur regularly during low-fee periods, early chain history, and in testnet environments. Any relayer or user attempting to prove coinbase inclusion in such a block hits this unconditionally. The entry path requires no privilege: any unprivileged NEAR account can call `verify_transaction_inclusion` with a valid on-chain block hash and an empty proof.

---

### Recommendation

Remove or condition the empty-proof guard. The correct fix is to allow the computation to proceed when the proof is empty, since `compute_root_from_merkle_proof` already handles this case correctly:

```rust
// Remove this line:
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

// The existing computation already handles empty proofs correctly:
merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

Alternatively, add a short-circuit before the guard: if `args.merkle_proof.is_empty()`, return `args.tx_id == header.block_header.merkle_root` directly.

---

### Proof of Concept

1. Submit a Bitcoin block header whose `merkle_root` equals a single transaction hash `T` (a coinbase-only block). This is a valid Bitcoin block.
2. Call `verify_transaction_inclusion` with:
   - `tx_id = T`
   - `tx_block_blockhash = <hash of the submitted block>`
   - `tx_index = 0`
   - `merkle_proof = []` (empty — correct for a single-tx block)
   - `confirmations = 1`
3. **Expected result**: `true` (the transaction is provably included; `compute_root_from_merkle_proof(T, 0, &[]) == T == merkle_root`).
4. **Actual result**: panic — `"Merkle proof is empty"` — due to the unconditional guard at line 315. [5](#0-4)

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

**File:** contract/src/lib.rs (L367-368)
```rust
        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
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
