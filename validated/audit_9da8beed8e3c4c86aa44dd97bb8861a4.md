### Title
ML-DSA-65 gas-key host functions charge exec fee against wire key length (1953 B) instead of trie-id length (33 B), diverging from the transaction-path fee by ~59× — (File: `runtime/near-vm-runner/src/logic/logic.rs`)

---

### Summary

The host functions `promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`, and `promise_batch_action_transfer_to_gas_key` pass the raw `public_key_len` argument (the borsh wire length of the key as supplied by the calling contract) directly to `gas_key_add_key_exec_fee` / `gas_key_transfer_exec_fee`. For an ML-DSA-65 key that value is **1953 bytes**. The transaction-level fee path in `runtime/runtime/src/config.rs` passes `action.public_key.trie_id_len()` = **33 bytes** to the same fee helpers. The exec fee is defined as "what the receiver needs to read/write in the trie"; the actual trie key for an ML-DSA-65 access key is 33 bytes (SHA3-256 hash form). The two code paths therefore compute a divergent fee for the identical on-chain operation, with the host-function path overcharging by a factor of ~36–59×.

---

### Finding Description

`PublicKey::trie_id_len()` was introduced precisely because ML-DSA-65 access keys are stored in the trie as a 32-byte SHA3-256 hash (tag + hash = 33 bytes), not as the 1952-byte raw pubkey. The documentation explicitly warns:

> "Callers that still use `len()` for trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes."

The transaction-level fee helpers were updated to call `trie_id_len()`:

- `total_send_fees` → `action.public_key.trie_id_len()` for `TransferToGasKey` / `WithdrawFromGasKey`
- `permission_exec_fees` → `public_key.trie_id_len()` for gas-key `AddKey`
- `exec_fee` → `action.public_key.trie_id_len()` for `TransferToGasKey` / `WithdrawFromGasKey`

But the three host functions were **not** updated. They forward the caller-supplied `public_key_len` directly:

```rust
// logic.rs – promise_batch_action_add_gas_key_with_full_access
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,   // ← 1953 for ML-DSA-65, should be 33
    num_nonces,
);
```

```rust
// logic.rs – promise_batch_action_transfer_to_gas_key
let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
let exec = gas_key_transfer_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,   // ← 1953 for ML-DSA-65, should be 33
);
```

The same pattern appears in the wasmtime runner (`wasmtime_runner/logic.rs` lines 3395–3399 and 3323–3325).

Inside `gas_key_add_key_exec_fee`, the per-byte component is:

```rust
let nonce_key_len =
    access_key_key_len(account_id_len, public_key_len) + size_of::<NonceIndex>();
let per_byte = cfg.fee(ActionCosts::gas_key_byte).exec_fee()
    .checked_mul((nonce_key_len + AccessKey::NONCE_VALUE_LEN) as u64)
    .unwrap()
    .checked_mul(num_nonces)
    .unwrap();
```

With `public_key_len = 1953` and `account_id_len = 10`, `nonce_key_len = 1967`; with `public_key_len = 33`, `nonce_key_len = 47`. For 100 nonces the per-byte component is charged against `(1967+8)×100 = 197 500` bytes instead of `(47+8)×100 = 5 500` bytes — a **35.9× overcharge**.

---

### Impact Explanation

Any contract that calls `promise_batch_action_add_gas_key_with_full_access` or `promise_batch_action_add_gas_key_with_function_call` with an ML-DSA-65 public key will be charged ~36–59× more gas than the equivalent transaction. Because the gas budget for a receipt is fixed at dispatch time, this can cause the receipt to exhaust its gas and abort, making it impossible for a contract to programmatically add ML-DSA-65 gas keys regardless of how much gas the caller attaches. The same overcharge applies to `promise_batch_action_transfer_to_gas_key`. The discrepancy is deterministic and consensus-safe (all nodes compute the same inflated fee), but it breaks the protocol invariant that the fee for an operation is the same whether it is submitted as a transaction or emitted by a contract.

---

### Likelihood Explanation

`PostQuantumSignatures` is a nightly/upcoming feature. Once it is stabilised, any contract that manages ML-DSA-65 gas keys on behalf of users will hit this overcharge on every call. The root cause is a straightforward missed update: the three host-function call sites were not changed when `trie_id_len()` was introduced, and there is no test that compares the gas charged by the host-function path against the transaction-path fee for an ML-DSA-65 key.

---

### Recommendation

Replace `public_key_len as usize` with `public_key.trie_id_len()` (after decoding the key) in all three host functions, mirroring the transaction-path callers:

```rust
// After: let public_key = self.get_public_key(public_key_ptr, public_key_len)?;
let decoded_key = public_key.decode()?;
let key_len_for_fees = decoded_key.trie_id_len();   // 33 for ML-DSA-65, unchanged for ed25519/secp256k1

let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    key_len_for_fees,   // ← was public_key_len as usize
    num_nonces,
);
```

Apply the same fix in `wasmtime_runner/logic.rs`. Add a test that asserts the gas