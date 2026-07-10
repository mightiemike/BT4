### Title
Deprecated `verify_transaction_inclusion` Remains Callable On-Chain with Incomplete Merkle Validation — (`contract/src/lib.rs`)

---

### Summary

The contract exposes two SPV verification entry points. The older one, `verify_transaction_inclusion`, was deprecated in v0.5.0 because it lacks the coinbase-proof check needed to block the 64-byte Merkle-proof forgery attack. The newer `verify_transaction_inclusion_v2` adds that check. However, Rust's `#[deprecated]` attribute is a **compiler warning only**; it imposes no runtime restriction. Any unprivileged NEAR caller can still invoke the v1 function directly, bypassing the coinbase guard entirely and obtaining a `true` result for a non-existent Bitcoin transaction.

---

### Finding Description

`verify_transaction_inclusion` (v1) validates only the transaction's own Merkle proof against the stored block Merkle root: [1](#0-0) 

```rust
require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

merkle_tools::compute_root_from_merkle_proof(
    args.tx_id,
    usize::try_from(args.tx_index).unwrap(),
    &args.merkle_proof,
) == header.block_header.merkle_root
```

It performs **one** check (tx proof vs. Merkle root) but omits the second check (coinbase proof vs. Merkle root) that `verify_transaction_inclusion_v2` adds to defeat the 64-byte internal-node forgery: [2](#0-1) 

The `#[deprecated]` attribute on v1 is a Rust lint, not an access control mechanism: [3](#0-2) 

The function remains `pub`, carries only `#[pause]` (not `#[private]`), and is therefore reachable by any NEAR account at any time the contract is unpaused: [4](#0-3) 

This is the direct analog to M-06: just as `recoverERC20` checked `tokenAddress != stakingToken` but omitted `tokenAddress != rewardsToken`, `verify_transaction_inclusion` checks the tx Merkle proof but omits the coinbase Merkle proof, leaving the second invariant unguarded.

---

### Impact Explanation

The 64-byte transaction forgery (documented at https://www.bitmex.com/blog/64-Byte-Transactions) allows an attacker to craft a 64-byte blob that is a valid internal Merkle-tree node and present it as a `tx_id`. Because v1 only recomputes the Merkle root from the supplied proof and compares it to the stored root, the function returns `true` for a transaction that does not exist in the Bitcoin block. Any NEAR smart contract that calls `verify_transaction_inclusion` to gate a value-bearing action (bridge withdrawal, token mint, settlement) can be deceived into executing that action for a fabricated Bitcoin transaction.

---

### Likelihood Explanation

The function is publicly callable by any NEAR account without any role or stake requirement. The forgery technique is well-known and has published tooling. The only runtime barrier is the `#[pause]` guard, which is inactive in normal operation. Any integrator that followed the original API and did not migrate to v2 is immediately exploitable.

---

### Recommendation

Replace the `#[deprecated]` annotation with a hard runtime panic so the function is unreachable on-chain:

```rust
pub fn verify_transaction_inclusion(&self, ...) -> bool {
    env::panic_str("verify_transaction_inclusion is removed; use verify_transaction_inclusion_v2");
}
```

Alternatively, inline the coinbase-proof check into v1 so both code paths are equally safe, and remove the deprecation.

---

### Proof of Concept

1. A real Bitcoin block `B` is stored in the contract (submitted via `submit_blocks`).
2. Attacker identifies the Merkle root `R` of block `B`.
3. Attacker constructs a 64-byte value `fake_tx` that is a valid internal Merkle-tree node of `B`, along with a sibling path `proof` such that `compute_root_from_merkle_proof(fake_tx, idx, proof) == R`.
4. Attacker calls `verify_transaction_inclusion` with `tx_id = fake_tx`, `tx_block_blockhash = hash(B)`, `tx_index = idx`, `merkle_proof = proof`, `confirmations = 1`.
5. The function passes the `!merkle_proof.is_empty()` guard, recomputes the root, finds it equal to `R`, and returns `true`.
6. A downstream NEAR contract that trusts this result executes a value-bearing action (e.g., minting wrapped BTC) for a Bitcoin transaction that never existed. [5](#0-4)

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

**File:** contract/src/lib.rs (L358-365)
```rust
        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );
```
