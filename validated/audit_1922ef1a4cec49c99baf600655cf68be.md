### Title
ML-DSA-65 `TransferToGasKey` / `AddGasKey` host functions charge exec fee against wire key length (1953 B) instead of trie-id length (33 B), producing ~59× overcharge vs. transaction path — (File: `runtime/near-vm-runner/src/logic/logic.rs`)

---

### Summary

The `promise_batch_action_transfer_to_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, and `promise_batch_action_add_gas_key_with_function_call` host functions compute the **exec-side** gas fee using the raw `public_key_len` parameter (the borsh wire length of the public key as passed by the contract). For ML-DSA-65 keys this is **1953 bytes**. The transaction-path exec fee for the identical action correctly calls `action.public_key.trie_id_len()`, which returns **33 bytes** (the on-trie SHA3-256 hash form). The divergence produces a ~59× overcharge on the exec fee for any contract that manages ML-DSA-65 gas keys via host functions, breaking the fee-domain invariant that the same action costs the same gas regardless of submission path.

---

### Finding Description

`gas_key_transfer_exec_fee` (and `gas_key_add_key_exec_fee`) in `core/parameters/src/cost.rs` are designed to price **receiver-side trie work**: they call `access_key_key_len(account_id_len, public_key_len)` to estimate the trie key length the receiver must read/write. For ML-DSA-65 keys the trie key stores a 33-byte SHA3-256 hash (tag `3` + 32-byte digest), not the 1953-byte full pubkey. The correct argument is therefore `trie_id_len()` = 33, not the wire length 1953.

**Transaction path — correct** (`runtime/runtime/src/config.rs`, lines 347–354):

```rust
TransferToGasKey(action) => {
    gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
        .total()
}
```

**Host-function path — wrong** (`runtime/near-vm-runner/src/logic/logic.rs`, lines 3091–3096):

```rust
let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,   // ← 1953 for ML-DSA-65; should be 33
);
```

The identical mistake appears in `runtime/near-vm-runner/src/wasmtime_runner/logic.rs` (lines 3323–3325) and in both `promise_batch_action_add_gas_key_with_full_access` (line 3158) and `promise_batch_action_add_gas_key_with_function_call` (line 3229) in the same files.

The send fee intentionally uses the wire length (the sender physically transmits the full pubkey), so that half is correct. Only the exec fee is wrong.

The documentation in `docs/architecture/how/post_quantum_signatures.md` (lines 126–138) explicitly states: *"Every storage-stake and trie-byte-priced fee path was updated to call `trie_id_len()`."* The host-function exec-fee paths were not updated.

---

### Impact Explanation

For an ML-DSA-65 gas key with a typical 10-byte account ID, the exec fee per-byte component is computed against a trie key length that is `1953 − 33 = 1920` bytes too large. The overcharge per action is `1920 × gas_key_byte_exec_fee`. At the current `gas_key_byte` exec fee rate this is a ~59× multiplier on the per-byte exec component relative to the transaction path. Contracts that manage ML-DSA-65 gas keys via `promise_batch_action_transfer_to_gas_key` or `promise_batch_action_add_gas_key_*` will exhaust their gas budget far sooner than expected, making ML-DSA-65 gas-key operations via host functions economically unviable and breaking the protocol invariant that the same action costs the same gas regardless of submission path.

---

### Likelihood Explanation

Both `PostQuantumSignatures` and `GasKeys` are stabilized at protocol version 85 (stable). Any contract that calls `promise_batch_action_transfer_to_gas_key` or `promise_batch_action_add_gas_key_*` with an ML-DSA-65 public key is affected. The combination is a valid, documented use case. The bug is triggered by any unprivileged contract call with a user-supplied ML-DSA-65 public key.

---

### Recommendation

In `promise_batch_action_transfer_to_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, and `promise_batch_action_add_gas_key_with_function_call` (both in `logic.rs` and `wasmtime_runner/logic.rs`), decode the public key first and then pass `decoded_key.trie_id_len()` to the exec-fee helpers, matching the transaction path:

```rust
let decoded_key = public_key.decode()?;
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    decoded_key.trie_id_len(),   // 33 for ML-DSA-65, not 1953
);
// ... then use decoded_key for append_action_transfer_to_gas_key
```

---

### Proof of Concept

1. Deploy a contract that calls `promise_batch_action_transfer_to_gas_key` with a borsh-encoded ML-DSA-65 public key (`public_key_len` = 1953).
2. The host function computes `gas_key_transfer_exec_fee(fees, receiver_id.len(), 1953)`.
3. The transaction path for the identical `TransferToGasKey(action)` computes `gas_key_transfer_exec_fee(fees, receiver_id.len(), 33)`.
4. The exec fee divergence is `(1953 − 33) × gas_key_byte_exec_fee × access_key_key_len_factor` per action — approximately 59× the correct exec fee on the per-byte component.
5. The existing test `test_gas_key_fee_parity` in `test-loop-tests/src/tests/gas_keys.rs` (lines 1037–1100) only exercises ED25519 keys (`KeyType::ED25519`), so it does not catch this divergence for ML-DSA-65. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3091-3096)
```rust
        let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
        let exec = gas_key_transfer_exec_fee(
            &self.fees_config,
            receiver_id.len(),
            public_key_len as usize,
        );
```

**File:** runtime/runtime/src/config.rs (L347-354)
```rust
        TransferToGasKey(action) => {
            gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
                .total()
        }
        WithdrawFromGasKey(action) => {
            gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
                .total()
        }
```

**File:** core/parameters/src/cost.rs (L814-828)
```rust
/// Send fee for TransferToGasKey / WithdrawFromGasKey actions.
/// Based on the public key length (what the sender sees).
pub fn gas_key_transfer_send_fee(
    cfg: &RuntimeFeesConfig,
    sender_is_receiver: bool,
    public_key_len: usize,
) -> GasKeyTransferFee {
    let base = cfg.fee(ActionCosts::gas_key_transfer_base).send_fee(sender_is_receiver);
    let per_byte = cfg
        .fee(ActionCosts::gas_key_byte)
        .send_fee(sender_is_receiver)
        .checked_mul(public_key_len as u64)
        .unwrap();
    GasKeyTransferFee { base, per_byte }
}
```

**File:** core/parameters/src/cost.rs (L833-847)
```rust
pub fn gas_key_transfer_exec_fee(
    cfg: &RuntimeFeesConfig,
    account_id_len: usize,
    public_key_len: usize,
) -> GasKeyTransferFee {
    let base = cfg.fee(ActionCosts::gas_key_transfer_base).exec_fee();
    let trie_key_len = access_key_key_len(account_id_len, public_key_len);
    let estimated_value_len = AccessKey::min_gas_key_borsh_len();
    let per_byte = cfg
        .fee(ActionCosts::gas_key_byte)
        .exec_fee()
        .checked_mul((trie_key_len + estimated_value_len) as u64)
        .unwrap();
    GasKeyTransferFee { base, per_byte }
}
```

**File:** core/crypto/src/signature.rs (L326-339)
```rust
    /// Length, in bytes, of the on-trie identifier for an access-key
    /// entry owned by this public key. For ed25519/secp256k1 this matches
    /// `len()`; for ML-DSA-65 the trie stores a SHA3-256 hash (33 bytes
    /// including the type tag), not the 1953-byte borsh-encoded pubkey.
    /// Used by storage-fee calculations on the runtime side; cheap to call
    /// (no hashing) - for ML-DSA-65 this returns the size of the digest
    /// form without actually hashing the pubkey.
    pub fn trie_id_len(&self) -> usize {
        match self {
            Self::ED25519(_) => 1 + ed25519_dalek::PUBLIC_KEY_LENGTH,
            Self::SECP256K1(_) => 1 + 64,
            Self::MLDSA65(_) => 1 + ML_DSA_65_HASH_LENGTH,
        }
    }
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3323-3326)
```rust
    let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
    let exec =
        gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
    let burn_base = send.base;
```

**File:** docs/architecture/how/post_quantum_signatures.md (L126-138)
```markdown
### 5. Storage usage and fee plumbing

The storage-stake calculation
(`runtime/runtime/src/access_keys.rs::access_key_storage_usage`) and the
gas-key fee helpers (`gas_key_*_fee` in `runtime/runtime/src/config.rs`) use
`PublicKey::trie_id_len()` rather than `PublicKey::len()`:

- `len()` reports the borsh-encoded length (33 / 65 / 1953 across the
  three `PublicKey` variants).
- `trie_id_len()` reports the on-trie length (33 / 65 / **33**).

The two diverge only for `PublicKey::MLDSA65`. Every storage-stake and
trie-byte-priced fee path was updated to call `trie_id_len()`.
```

**File:** test-loop-tests/src/tests/gas_keys.rs (L1037-1046)
```rust
fn test_gas_key_fee_parity(mode: GasKeyKind) {
    let mut setup = setup_host_function_test();
    let account = setup.account.clone();

    let num_nonces: NonceIndex = 4;
    let gas_key_a_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_tx").into();
    let gas_key_b_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_host_fn").into();

```
