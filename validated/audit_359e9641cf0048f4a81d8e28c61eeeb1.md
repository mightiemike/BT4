### Title
Host-function gas-key exec-fee uses wire length instead of trie-id length for ML-DSA-65 keys, diverging from the transaction path - (`runtime/near-vm-runner/src/logic/logic.rs`)

### Summary

The `promise_batch_action_transfer_to_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, and `promise_batch_action_add_gas_key_with_function_call` host functions pass the raw wire/borsh length of the caller-supplied public key bytes (`public_key_len as usize`) to `gas_key_transfer_exec_fee` and `gas_key_add_key_exec_fee`. For ML-DSA-65 keys this is 1953 bytes. The transaction path in `exec_fee()` correctly passes `action.public_key.trie_id_len()` = 33 bytes. The exec fee is supposed to model the trie key the receiver must read/write; for ML-DSA-65 that key is a 33-byte SHA3-256 hash, not the 1953-byte full pubkey. The result is a ~59× exec-fee overcharge for any contract that calls these host functions with an ML-DSA-65 gas key.

### Finding Description

`PublicKey::trie_id_len()` and `PublicKey::len()` diverge only for `ML-DSA-65`:

| Variant | `len()` (wire) | `trie_id_len()` (on-trie) |
|---|---|---|
| ED25519 | 33 | 33 |
| SECP256K1 | 65 | 65 |
| ML-DSA-65 | 1953 | 33 |

The transaction path in `exec_fee()` correctly uses `trie_id_len()`:

```rust
// runtime/runtime/src/config.rs  lines 347-354
TransferToGasKey(action) => {
    gas_key_transfer_exec_fee(fees, receiver_id.len(), action.public_key.trie_id_len())
        .total()
}
```

The host-function path uses the raw caller-supplied byte count instead:

```rust
// runtime/near-vm-runner/src/logic/logic.rs  lines 3092-3095
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,   // ← wire length, not trie_id_len
);
```

`gas_key_transfer_exec_fee` feeds `public_key_len` directly into `access_key_key_len`, which computes the trie key length:

```rust
// core/parameters/src/cost.rs  lines 839-844
let trie_key_len = access_key_key_len(account_id_len, public_key_len);
let per_byte = cfg.fee(ActionCosts::gas_key_byte).exec_fee()
    .checked_mul((trie_key_len + estimated_value_len) as u64)
    .unwrap();
```

The same pattern repeats in `gas_key_add_key_exec_fee` calls inside `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call`, and identically in the wasmtime runner at `runtime/near-vm-runner/src/wasmtime_runner/logic.rs` lines 3324-3325 and 3395-3399.

### Impact Explanation

For an ML-DSA-65 gas key:
- Correct exec fee uses trie key length = `2 + account_id_len + 33` bytes
- Actual exec fee charged uses `2 + account_id_len + 1953` bytes
- Overcharge per call ≈ `1920 × gas_key_byte_exec_fee`

A contract that budgets gas based on the expected fee (matching the transaction path) will exhaust gas when calling these host functions with ML-DSA-65 keys. The fee inconsistency also breaks the protocol invariant that the same action costs the same gas regardless of whether it is submitted as a top-level transaction or dispatched from a contract via a host function. The existing `test_gas_key_fee_parity` test only exercises ED25519 keys and does not catch this divergence.

### Likelihood Explanation

`PostQuantumSignatures` is stabilized at protocol version 85 and active on mainnet. Any contract that manages ML-DSA-65 gas keys programmatically (e.g., a key-management or relayer contract) will hit this overcharge. The caller controls the public key bytes and their length, so the divergent value is directly user-controlled.

### Recommendation

Replace `public_key_len as usize` with the decoded key's `trie_id_len()` when computing exec fees in all three host functions:

```rust
// After decoding the public key:
let public_key_decoded = public_key.decode()?;
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_decoded.trie_id_len(),   // ← use trie_id_len, not wire length
);
```

Apply the same fix to `gas_key_add_key_exec_fee` calls in `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call` in both `logic/logic.rs` and `wasmtime_runner/logic.rs`. Add a `test_gas_key_fee_parity` variant that uses `KeyType::MLDSA65` to prevent regression.

### Proof of Concept

1. Deploy a contract that calls `promise_batch_action_transfer_to_gas_key` with an ML-DSA-65 public key (1953 borsh bytes).
2. Submit the same `TransferToGasKey` action as a top-level transaction signed with the same ML-DSA-65 key.
3. Compare `gas_burnt` on the action execution receipt.

Expected (transaction path): exec fee uses `trie_id_len` = 33 → `access_key_key_len(account_id_len, 33)`.
Actual (host function path): exec fee uses wire length = 1953 → `access_key_key_len(account_id_len, 1953)`.

The host-function receipt burns approximately `1920 × gas_key_byte_exec_fee` more gas than the transaction receipt, violating the parity invariant asserted by `test_gas_key_fee_parity`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

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

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3155-3160)
```rust
        let exec_fee = gas_key_add_key_exec_fee(
            &self.fees_config,
            receiver_id.len(),
            public_key_len as usize,
            num_nonces,
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

**File:** runtime/runtime/src/config.rs (L389-395)
```rust
    let nonce_fee = gas_key_add_key_exec_fee(
        fees,
        account_id.len(),
        public_key.trie_id_len(),
        gas_key_info.num_nonces,
    );
    key_fee.checked_add(nonce_fee.total()).unwrap()
```

**File:** core/parameters/src/cost.rs (L833-846)
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

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3323-3325)
```rust
    let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
    let exec =
        gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
```
