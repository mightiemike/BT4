### Title
Deprecated `verify_transaction_inclusion` Remains Publicly Callable, Bypassing the Coinbase-Proof Forgery Guard Added in `verify_transaction_inclusion_v2` — (File: `contract/src/lib.rs`)

---

### Summary

`verify_transaction_inclusion_v2` was introduced specifically to close the 64-byte transaction Merkle-proof forgery vulnerability by requiring a coinbase Merkle proof. However, the original `verify_transaction_inclusion` function remains a fully callable public entry point. Any unprivileged NEAR caller can invoke it directly, bypassing the coinbase-proof check entirely and exploiting the known forgery path that `v2` was designed to prevent.

---

### Finding Description

`verify_transaction_inclusion_v2` enforces a critical security invariant: before accepting a transaction inclusion proof, it requires the caller to also supply a valid coinbase Merkle proof rooted at the same block's `merkle_root`. [1](#0-0) 

This coinbase-proof requirement is the mitigation for the 64-byte transaction Merkle-proof forgery attack (documented at https://www.bitmex.com/blog/64-Byte-Transactions). Without it, an attacker can craft a 64-byte blob whose double-SHA256 hash equals an internal node of the Merkle tree, then supply a valid Merkle path for that internal node, causing the verifier to return `true` for a transaction that was never included in the block.

The deprecated `verify_transaction_inclusion` function does **not** perform this coinbase-proof check. [2](#0-1) 

The `#[deprecated]` Rust attribute is a **compiler lint only**; it does not restrict runtime access. The function is `pub`, carries `#[pause]` (same as `v2`), and is reachable by any NEAR account that can call the contract. The function's own docstring acknowledges the gap:

> *"This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash."* [3](#0-2) 

The structural parallel to the reported TITN bug is exact:

| TITN contract | BTC light client |
|---|---|
| `transfer`/`transferFrom` enforce `_validateTransfer` | `verify_transaction_inclusion_v2` enforces coinbase-proof check |
| `mint`/`burn` (bridge path) bypass `_validateTransfer` | `verify_transaction_inclusion` (direct path) bypasses coinbase-proof check |
| Both paths move tokens | Both paths return a `bool` consumed as proof of inclusion |

---

### Impact Explanation

A downstream NEAR contract (bridge, DEX, lending protocol) that calls `verify_transaction_inclusion` and receives `true` will incorrectly conclude that a Bitcoin transaction was confirmed on-chain. An attacker can:

1. Identify any confirmed Bitcoin block whose `merkle_root` is known to the light client.
2. Construct a 64-byte payload whose `SHA256d` equals an internal Merkle-tree node of that block.
3. Supply a valid Merkle path from that internal node to the `merkle_root`.
4. Call `verify_transaction_inclusion` with this forged `tx_id` and proof.
5. The function returns `true`; the downstream contract releases funds, mints tokens, or executes an irreversible action for a transaction that never existed.

The corrupted value is the boolean proof result returned to any consumer of `verify_transaction_inclusion`. The invariant broken is: *"a `true` result means the supplied `tx_id` is a real leaf-level transaction included in a mainchain Bitcoin block."*

---

### Likelihood Explanation

- The entry point is fully public and requires no privileged role.
- The 64-byte forgery technique is well-documented and has published tooling.
- Any protocol that integrated the light client before `v2` was added, or that calls `verify_transaction_inclusion` for any reason (e.g., following the deprecated function's own interface), is immediately exploitable.
- No chain reorganization, key compromise, or social engineering is required.

---

### Recommendation

Remove or gate the deprecated function so the insecure path is unreachable:

1. **Preferred**: Delete `verify_transaction_inclusion` from the public API entirely, or mark it `#[private]` so only the contract itself can call it (as `verify_transaction_inclusion_v2` already does internally).
2. **Alternative**: Add the same coinbase-proof check inside `verify_transaction_inclusion` so both paths enforce the invariant, making the bypass impossible regardless of which entry point is used.

---

### Proof of Concept

```
// Attacker calls the deprecated public function directly, skipping the coinbase proof gate.

// 1. Pick any mainchain block hash known to the contract.
let target_block = <known mainchain blockhash>;

// 2. Craft a 64-byte payload P such that SHA256d(P) == some internal Merkle node N.
//    Compute the Merkle path from N up to merkle_root.
let forged_tx_id   = N;          // internal node hash, not a real transaction
let forged_proof   = [sibling_of_N, ...];  // valid path to merkle_root
let forged_index   = <index matching N's position>;

// 3. Call the deprecated (but still public) entry point — no coinbase proof required.
let result = contract.verify_transaction_inclusion(ProofArgs {
    tx_id:              forged_tx_id,
    tx_block_blockhash: target_block,
    tx_index:           forged_index,
    merkle_proof:       forged_proof,
    confirmations:      1,
});

// result == true  ← forged transaction accepted as confirmed
```

`verify_transaction_inclusion_v2` would reject this call at the coinbase-proof `require!` on line 359–365. `verify_transaction_inclusion` has no such gate and returns `true`. [4](#0-3)

### Citations

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L287-323)
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
