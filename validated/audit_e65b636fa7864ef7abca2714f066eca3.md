### Title
Wire-format `public_key_len` used instead of `trie_id_len` in gas-key host-function fee calculation for ML-DSA-65 keys — (`runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

---

### Summary

The `promise_batch_action_transfer_to_gas_key` and `promise_batch_action_add_gas_key_with_full_access` host functions pass the raw borsh wire-format `public_key_len` (1953 bytes for ML-DSA-65) directly to `gas_key_transfer_send_fee`, `gas_key_transfer_exec_fee`, and `gas_key_add_key_exec_fee`. The transaction path for the same actions correctly calls `action.public_key.trie_id_len()` (33 bytes for ML-DSA-65). For ed25519/secp256k1 the two values are identical, so the discrepancy is invisible until an ML-DSA-65 gas key is used via a contract host function. The result is a ~59× overcharge in the per-byte gas component, breaking the documented fee-parity invariant and causing contracts that budget gas based on the transaction-path cost to run out of gas.

---

### Finding Description

ML-DSA-65 access keys are stored in the trie by their SHA3-256 hash (33 bytes including the type tag), not by the full 1952-byte pubkey. `PublicKey::trie_id_len()` returns 33 for `MLDSA65` and 1953 for `len()`. [1](#0-0) 

The **transaction path** in `total_send_fees` and `exec_fee` correctly passes `trie_id_len()` to both fee helpers: [2](#0-1) [3](#0-2) 

The **host-function path** in `VMLogic::promise_batch_action_transfer_to_gas_key` passes the raw `public_key_len` argument (the borsh wire-format byte count supplied by the contract) to the same fee helpers: [4](#0-3) 

The wasmtime path has the identical defect: [5](#0-4) 

`gas_key_transfer_exec_fee` uses `public_key_len` to compute the trie key length: [6](#0-5) 

For ML-DSA-65, `public_key_len = 1953` but the actual trie key uses 33 bytes, so `access_key_key_len(account_id_len, 1953)` is computed instead of `access_key_key_len(account_id_len, 33)` — an overcount of 1920 bytes per call.

`promise_batch_action_add_gas_key_with_full_access` has the same defect, passing `public_key_len` to `gas_key_add_key_exec_fee`: [7](#0-6) 

`gas_key_add_key_exec_fee` multiplies the inflated key length by `num_nonces`, compounding the overcharge: [8](#0-7) 

The design document explicitly states that every storage-stake and trie-byte-priced fee path must call `trie_id_len()`, and that callers still using `len()` will misprice ML-DSA-65 keys by ~1900 bytes: [9](#0-8) 

The existing fee-parity test only exercises ED25519 keys (for which `len() == trie_id_len()`), so the divergence is not caught: [10](#0-9) 

---

### Impact Explanation

For an ML-DSA-65 gas key and a 10-byte account ID:

| Path | `public_key_len` passed | trie key bytes charged |
|---|---|---|
| Transaction (`trie_id_len`) | 33 | 45 |
| Host function (`public_key_len`) | 1953 | 1965 |

The per-byte component of both the send fee and the exec fee is overcharged by 1920 bytes (~43× the correct value). For `promise_batch_action_add_gas_key_with_full_access` with `num_nonces` nonces the overcharge is multiplied by `num_nonces`. Any contract that budgets gas for these host functions based on the transaction-path cost will exhaust its gas allowance, causing the receipt to fail. Contracts that do not fail will pay far more gas than the equivalent transaction, breaking economic predictability for ML-DSA-65 gas-key users.

---

### Likelihood Explanation

`PostQuantumSignatures` is stabilized at protocol version 85 and `GasKeys` is also stable. Both features are active on mainnet. Any contract that holds or manages ML-DSA-65 gas keys and calls `promise_batch_action_transfer_to_gas_key` or `promise_batch_action_add_gas_key_with_full_access` will trigger the overcharge. The bug requires no special privilege — any unprivileged contract can call these host functions with a caller-controlled ML-DSA-65 public key.

---

### Recommendation

After deserializing the public key in both host functions, use the decoded key's `trie_id_len()` instead of the raw `public_key_len` argument when invoking the fee helpers. Concretely, decode the key before the fee calculation and replace `public_key_len as usize` with `decoded_key.trie_id_len()` in the calls to `gas_key_transfer_send_fee`, `gas_key_transfer_exec_fee`, and `gas_key_add_key_exec_fee`. Add a fee-parity test that exercises ML-DSA-65 keys alongside the existing ED25519 test.

---

### Proof of Concept

1. Add an ML-DSA-65 full-access gas key to account `alice` via an `AddKey` transaction (permitted after `PostQuantumSignatures` activates).
2. Deploy a contract that calls `promise_batch_action_transfer_to_gas_key` with the ML-DSA-65 key (borsh-encoded: 1953 bytes) and a small deposit.
3. Measure `gas_burnt` on the resulting receipt.
4. Issue the equivalent `TransferToGasKey` transaction directly.
5. Observe that step 3 burns ~59× more gas in the per-byte component than step 4, and that the contract call fails with `GasExceeded` if its attached gas was sized to match the transaction-path cost.

The divergence is exact: `public_key_len = 1953` vs `trie_id_len = 33`; the fee functions `gas_key_transfer_send_fee` and `gas_key_transfer_exec_fee` are called with the wrong value in the host-function path. [11](#0-10) [12](#0-11)

### Citations

**File:** core/crypto/src/signature.rs (L268-274)
```rust
    pub fn len(&self) -> usize {
        match self {
            Self::ED25519(_) => 1 + ed25519_dalek::PUBLIC_KEY_LENGTH,
            Self::SECP256K1(_) => 1 + 64,
            Self::MLDSA65(_) => 1 + ML_DSA_65_PUBLIC_KEY_LENGTH,
        }
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

**File:** runtime/runtime/src/config.rs (L114-117)
```rust
            TransferToGasKey(action) => {
                gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
                    .total()
            }
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

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3091-3096)
```rust
        let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
        let exec = gas_key_transfer_exec_fee(
            &self.fees_config,
            receiver_id.len(),
            public_key_len as usize,
        );
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3323-3325)
```rust
    let send = gas_key_transfer_send_fee(&ctx.fees_config, sir, public_key_len as usize);
    let exec =
        gas_key_transfer_exec_fee(&ctx.fees_config, receiver_id.len(), public_key_len as usize);
```

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3395-3400)
```rust
    let exec_fee = gas_key_add_key_exec_fee(
        &ctx.fees_config,
        receiver_id.len(),
        public_key_len as usize,
        num_nonces,
    );
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

**File:** core/parameters/src/cost.rs (L879-897)
```rust
pub fn gas_key_add_key_exec_fee(
    cfg: &RuntimeFeesConfig,
    account_id_len: usize,
    public_key_len: usize,
    num_nonces: NonceIndex,
) -> GasKeyAddFee {
    let num_nonces = num_nonces as u64;
    let base =
        cfg.fee(ActionCosts::gas_key_nonce_write_base).exec_fee().checked_mul(num_nonces).unwrap();
    let nonce_key_len =
        access_key_key_len(account_id_len, public_key_len) + std::mem::size_of::<NonceIndex>();
    let per_byte = cfg
        .fee(ActionCosts::gas_key_byte)
        .exec_fee()
        .checked_mul((nonce_key_len + AccessKey::NONCE_VALUE_LEN) as u64)
        .unwrap()
        .checked_mul(num_nonces)
        .unwrap();
    GasKeyAddFee { base, per_byte }
```

**File:** docs/architecture/how/post_quantum_signatures.md (L189-191)
```markdown
2. **`PublicKey::trie_id_len()` is a new contract** that all
   storage-cost code must respect. Callers that still use `len()` for
   trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes.
```

**File:** test-loop-tests/src/tests/gas_keys.rs (L1042-1045)
```rust
    let gas_key_a_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_tx").into();
    let gas_key_b_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_host_fn").into();
```
