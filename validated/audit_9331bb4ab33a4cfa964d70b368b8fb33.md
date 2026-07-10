### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing 64-Byte Transaction Forgery Protection — (`contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion` is still a live, publicly reachable NEAR entry point despite being marked `#[deprecated]`. It lacks the coinbase Merkle proof check that `verify_transaction_inclusion_v2` requires. Any unprivileged NEAR caller can invoke the deprecated endpoint directly, bypassing the 64-byte transaction forgery mitigation and receiving a `true` return value for a fabricated transaction inclusion proof.

---

### Finding Description

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle proof forgery vulnerability (https://www.bitmex.com/blog/64-Byte-Transactions). Its protection is a mandatory coinbase proof check:

```rust
// contract/src/lib.rs  lines 358-365
require!(
    merkle_tools::compute_root_from_merkle_proof(
        args.coinbase_tx_id.clone(),
        0usize,
        &args.coinbase_merkle_proof,
    ) == header.block_header.merkle_root,
    "Incorrect coinbase merkle proof"
);
``` [1](#0-0) 

The original `verify_transaction_inclusion`, however, contains no such check. Despite the `#[deprecated]` attribute (which in Rust is a lint warning only, not a compile-time or runtime restriction), the function is still declared `pub` and gated only by `#[pause]`:

```rust
// contract/src/lib.rs  lines 283-323
#[deprecated(since = "0.5.0", note = "Use `verify_transaction_inclusion_v2` instead.")]
#[pause]
pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
    ...
    require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");
    merkle_tools::compute_root_from_merkle_proof(
        args.tx_id,
        usize::try_from(args.tx_index).unwrap(),
        &args.merkle_proof,
    ) == header.block_header.merkle_root
}
``` [2](#0-1) 

The `compute_root_from_merkle_proof` function in `merkle-tools` simply walks a proof path and returns the computed root:

```rust
// merkle-tools/src/lib.rs  lines 34-52
pub fn compute_root_from_merkle_proof(
    transaction_hash: H256,
    transaction_position: usize,
    merkle_proof: &Vec<H256>,
) -> H256 { ... }
``` [3](#0-2) 

Bitcoin's Merkle tree internal nodes are always exactly 64 bytes (two concatenated 32-byte child hashes). An attacker can take any such internal node `N` from a real mainchain block, supply a valid proof path `P` such that `compute_root_from_merkle_proof(N, idx, P) == merkle_root`, and call `verify_transaction_inclusion` directly. The function returns `true`, falsely asserting that `N` is a confirmed transaction.

The parallel to the external report is exact: `modifyListings`/`cancelListings` had type-preservation checks that `relist` omitted; here `verify_transaction_inclusion_v2` has the coinbase-proof check that `verify_transaction_inclusion` omits. In both cases the unguarded function is still reachable by any unprivileged caller.

---

### Impact Explanation

Any downstream NEAR contract or application that calls `verify_transaction_inclusion` to gate a fund release (bridge, cross-chain swap, etc.) will accept a forged SPV proof. The corrupted protocol value is the boolean return of `verify_transaction_inclusion`: it is `true` for a fabricated internal-node "transaction." An attacker who can forge this proof can claim assets that correspond to a Bitcoin transaction that never existed on-chain.

---

### Likelihood Explanation

- No privileged role is required. Any NEAR account can call `verify_transaction_inclusion` directly.
- The 64-byte forgery technique is publicly documented and the required inputs (an internal Merkle node and its proof path) are trivially derivable from any mainchain block with ≥ 2 transactions.
- The contract's own documentation acknowledges the risk in the function's warning comment at line 278–280, confirming the attack surface is known and real. [4](#0-3) 

---

### Recommendation

Remove `verify_transaction_inclusion` from the public ABI entirely, or add `#[private]` to prevent external calls. If the internal call from `verify_transaction_inclusion_v2` must be preserved, extract the shared Merkle-root comparison into a private helper and delete the public deprecated entry point. At minimum, add the same coinbase proof requirement to `verify_transaction_inclusion` so both paths are equally protected.

---

### Proof of Concept

1. Pick any mainchain block `B` with ≥ 2 transactions. Its Merkle root `R` is stored in `headers_pool`.
2. Compute any internal Merkle tree node `N` (64 bytes = two child hashes concatenated and double-SHA256'd) and a proof path `P` at position `idx` such that `compute_root_from_merkle_proof(N, idx, P) == R`.
3. Call (via NEAR CLI or a contract):
   ```
   verify_transaction_inclusion({
     tx_id: N,
     tx_block_blockhash: B,
     tx_index: idx,
     merkle_proof: P,
     confirmations: 1
   })
   ```
4. The function returns `true` — a forged proof accepted — because no coinbase check exists in this path.

`verify_transaction_inclusion_v2` called with the same `tx_id = N` would reject the call at the coinbase proof `require!`, demonstrating that the protection exists in the newer function but is entirely absent in the still-callable deprecated one. [5](#0-4)

### Citations

**File:** contract/src/lib.rs (L276-281)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
    /// # Panics
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
