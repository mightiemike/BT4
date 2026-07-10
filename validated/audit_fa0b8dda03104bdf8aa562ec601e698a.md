### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase-Proof Guard Against 64-Byte Merkle Forgery — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is marked `#[deprecated]` but carries **no runtime access restriction**. Any unprivileged NEAR caller can invoke it directly, completely skipping the coinbase-merkle-proof guard that `verify_transaction_inclusion_v2` adds to defeat the 64-byte Merkle-proof forgery attack. The result is that an attacker can obtain a `true` SPV-inclusion verdict for a Bitcoin transaction that was never confirmed, corrupting every downstream consumer (bridge, oracle, cross-chain protocol) that trusts the light client's output.

---

### Finding Description

`verify_transaction_inclusion` (lines 283–323, `contract/src/lib.rs`) is decorated with `#[deprecated]` and `#[pause]`. The `#[deprecated]` attribute is a **compile-time hint only**; it emits a Rust compiler warning but imposes zero runtime barrier. The `#[pause]` decorator only blocks calls when the contract is administratively paused. Neither attribute prevents an unprivileged NEAR account from calling the function at any time during normal operation. [1](#0-0) 

The function's own documentation acknowledges the gap:

> This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash. [2](#0-1) 

`verify_transaction_inclusion_v2` closes this gap by first verifying a coinbase merkle proof — proving that a real transaction (the coinbase, always at index 0) anchors the same merkle root — before delegating to the v1 path. [3](#0-2) 

Because `verify_transaction_inclusion` is still a live public entry point, the coinbase-proof guard is entirely optional from the caller's perspective. A caller who invokes v1 directly skips it unconditionally.

The core verification in both paths is `compute_root_from_merkle_proof`: [4](#0-3) 

This function starts from the caller-supplied `transaction_hash` and iteratively hashes it with the proof siblings. If the caller supplies an **internal Merkle-tree node** as `tx_id` (rather than a real transaction hash), and supplies a proof path that leads from that node to the root, the function returns the block's actual `merkle_root`. The v1 function then returns `true`, asserting that a non-existent transaction is confirmed in the block.

The only guards in v1 are:
- `confirmations <= gc_threshold` (a range check, not a forgery guard)
- `mainchain_header_to_height` membership (block must be on the main chain — correct, but irrelevant to forgery)
- `!args.merkle_proof.is_empty()` (trivially satisfied by any non-empty forged proof) [5](#0-4) 

None of these checks prevent the 64-byte Merkle forgery. The coinbase-proof check in v2 is the only guard that does, and it is absent from v1.

---

### Impact Explanation

Any NEAR smart contract or off-chain relayer that calls `verify_transaction_inclusion` (v1) instead of `verify_transaction_inclusion_v2` receives a forged `true` result for a Bitcoin transaction that was never confirmed. Bridge contracts that gate asset releases on a `true` SPV result from this light client would release funds against a fraudulent proof. The corrupted canonical value is the **proof-verification result** (`bool`) returned to the consuming contract — the same class of impact as the reference report's corrupted reward-eligibility state.

---

### Likelihood Explanation

The function is publicly exposed with no runtime restriction. The 64-byte Merkle forgery

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
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
