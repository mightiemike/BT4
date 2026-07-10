### Title
Deprecated `verify_transaction_inclusion` Skips Coinbase Merkle Proof Validation, Enabling 64-Byte Transaction Forgery — (`contract/src/lib.rs`)

### Summary

The contract exposes two equivalent transaction-inclusion verification paths: the deprecated `verify_transaction_inclusion` and the current `verify_transaction_inclusion_v2`. The v2 path adds coinbase Merkle proof validation to block the known 64-byte transaction forgery attack. The deprecated path omits this check entirely and remains callable by any unprivileged NEAR account. A proof submitter or recipient contract can deliberately invoke the deprecated path to bypass the coinbase proof guard and have the contract return `true` for a forged transaction inclusion proof.

### Finding Description

`verify_transaction_inclusion` (lines 288–322 of `contract/src/lib.rs`) is still a live, callable contract method. It is decorated with `#[pause]` (meaning it can be paused by an admin, but is active by default) and `#[deprecated]`, but neither attribute removes it from the contract's public ABI.

The function's own inline documentation states:

> "This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash." [1](#0-0) 

The root cause is that the function calls only `merkle_tools::compute_root_from_merkle_proof` and compares the result to `header.block_header.merkle_root`, with no coinbase transaction proof step: [2](#0-1) 

`verify_transaction_inclusion_v2`, by contrast, was introduced specifically to add the coinbase Merkle proof validation that closes the 64-byte transaction forgery vector described at https://www.bitmex.com/blog/64-Byte-Transactions. [3](#0-2) 

The two paths are semantically equivalent from a caller's perspective (same inputs, same boolean output, same on-chain state read), but only one enforces the coinbase guard. Any caller — including a recipient smart contract that integrates the light client — can freely choose the unguarded path.

### Impact Explanation

The 64-byte transaction attack allows an attacker to craft a 64-byte input that is simultaneously a valid serialized internal Merkle node and a plausible transaction ID. By supplying such an input to `verify_transaction_inclusion`, the contract returns `true` for a Bitcoin transaction that was never included in any block. Any downstream NEAR contract that gates asset releases, bridge withdrawals, or state transitions on a `true` result from this function can be exploited to authorize actions backed by a completely fabricated Bitcoin transaction.

**Impact category:** Proof-verification forgery → unauthorized asset movement or state change in any integrating contract.

### Likelihood Explanation

The entry point is a public NEAR contract method requiring no privileged keys. The 64-byte forgery technique is publicly documented and has known construction methods. An attacker only needs to: (1) identify an integrating contract that calls `verify_transaction_inclusion`, (2) construct a valid 64-byte internal-node preimage, and (3) submit a NEAR transaction calling the deprecated method with the forged `tx_id`. No special access is required.

### Recommendation

Remove `verify_transaction_inclusion` from the contract's public ABI entirely, or add a hard `env::panic_str` at its entry point so it is unconditionally non-callable regardless of the pause state. Do not rely on `#[deprecated]` or `#[pause]` as security controls — both leave the method reachable by default.

### Proof of Concept

1. Attacker identifies a NEAR contract `Victim` that calls `btc_light_client.verify_transaction_inclusion(args)` and releases funds on `true`.
2. Attacker constructs a 64-byte value `fake_tx_id` that, when hashed with the supplied `merkle_proof` siblings, produces a result equal to a real block's `merkle_root` (standard 64-byte Merkle forgery construction).
3. Attacker calls `btc_light_client.verify_transaction_inclusion({ tx_id: fake_tx_id, tx_block_blockhash: <real block>, tx_index: <crafted>, merkle_proof: <crafted>, confirmations: 1 })`.
4. The contract computes `compute_root_from_merkle_proof(fake_tx_id, ...)` → matches `header.block_header.merkle_root` → returns `true`.
5. `Victim` receives `true` and releases assets for a Bitcoin transaction that does not exist.

The deprecated path is the necessary vulnerable step: the same call routed to `verify_transaction_inclusion_v2` would fail the additional coinbase proof check and return `false`.

### Citations

**File:** contract/src/lib.rs (L264-270)
```rust
    ///
    /// # Deprecated
    /// Use [`verify_transaction_inclusion_v2`] instead, which includes coinbase merkle proof validation
    /// to mitigate the 64-byte transaction Merkle proof forgery vulnerability:
    /// https://www.bitmex.com/blog/64-Byte-Transactions
    ///
    /// @param `tx_id` transaction identifier
```

**File:** contract/src/lib.rs (L276-280)
```rust
    ///
    /// # Warning
    /// This function may return `true` if the provided `tx_id` is a hash of an internal node in the Merkle tree rather than a valid transaction hash.
    /// We assume that validation of whether the `tx_id` corresponds to a valid transaction hash is performed at a higher level of verification.
    ///
```

**File:** contract/src/lib.rs (L317-323)
```rust
        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```
