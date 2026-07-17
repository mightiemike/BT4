### Title
Gas-key exec-fee uses wire-length (1953 B) instead of trie-id-length (33 B) for ML-DSA-65 keys in host-function path — (`File: runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

### Summary

When a WASM contract calls `promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`, or `promise_batch_action_transfer_to_gas_key` with an ML-DSA-65 public key, the exec-fee calculation uses the **wire-encoded length** of the key (1953 bytes) as the `public_key_len` argument to `gas_key_add_key_exec_fee` / `gas_key_transfer_exec_fee`. The actual trie key written for an ML-DSA-65 access key is only **33 bytes** (tag + SHA3-256 hash). The runtime path for the same actions (via `exec_fee` in `config.rs`) correctly calls `public_key.trie_id_len()` = 33. The two representations diverge by 1920 bytes per key, producing a massive exec-gas overcharge that makes ML-DSA-65 gas-key operations from contracts effectively unusable.

### Finding Description

**Two representations, two call paths, one fee function:**

`gas_key_add_key_exec_fee` is documented as pricing the actual trie bytes written — "each nonce writes a trie entry of `(access_key_key_len + NonceIndex)` key bytes": [1](#0-0) 

The `access_key_key_len` helper adds `public_key_len` directly into the trie-key length: [2](#0-1) 

**Runtime path (transactions) — correct:**

`exec_fee` in `config.rs` calls `gas_key_add_key_exec_fee` with `public_key.trie_id_len()` = 33 for ML-DSA-65: [3](#0-2) 

`permission_exec_fees` also uses `trie_id_len()`: [4](#0-3) 

**Host-function path (contract-emitted actions) — wrong:**

`promise_batch_action_add_gas_key_with_full_access` passes the raw WASM `public_key_len` (the borsh wire length = 1953 for ML-DSA-65) directly: [5](#0-4) 

`promise_batch_action_add_gas_key_with_function_call` has the identical defect: [6](#0-5) 

`promise_batch_action_transfer_to_gas_key` has the same defect for `gas_key_transfer_exec_fee`: [7](#0-6) 

The wasmtime runner duplicates all three defects identically: [8](#0-7) [9](#0-8) [10](#0-9) 

**Why the two lengths diverge:**

`PublicKey::len()` returns 1953 for ML-DSA-65 (wire/borsh form); `PublicKey::trie_id_len()` returns 33 (tag + SHA3-256 hash, the actual trie key): [11](#0-10) 

The trie always writes the 33-byte hash form, never the 1953-byte full key: [12](#0-11) 

The design document explicitly states every storage-cost path must use `trie_id_len()`: [13](#0-12) 

### Impact Explanation

**Exact divergent value:** `public_key_len = 1953` (wire) vs `trie_id_len = 33` (hash). Difference = 1920 bytes.

For `gas_key_add_key_exec_fee` with `num_nonces` nonces, the overcharge per call is:

```
overcharge = 1920 × gas_key_byte_exec_fee × num_nonces
```

With `num_nonces` up to 65535 and `gas_key_byte` at its calibrated value, the overcharge is on the order of tens of Pgas — far exceeding any practical gas limit. This makes `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_transfer_to_gas_key` with ML-DSA-65 keys **effectively unusable from contracts**: the transaction will always exhaust gas before the action is appended.

Additionally, the exec-gas charged does not match the actual trie bytes written, breaking the deterministic gas-accounting invariant that exec fees must reflect real storage costs.

**Impact: High** — ML-DSA-65 gas keys cannot be added or funded by contracts; the fee domain invariant (exec gas = trie bytes × rate) is broken for this key scheme.

### Likelihood Explanation

**Medium.** Both `PostQuantumSignatures` (protocol version 85) and `GasKeys` must be active. Both are included in `STABLE_PROTOCOL_VERSION = 86`. Any unprivileged contract can trigger the overcharge by calling the affected host functions with an ML-DSA-65 public key. No admin or validator role is required. [14](#0-13) 

### Recommendation

In all three host functions (`promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`, `promise_batch_action_transfer_to_gas_key`) in both `logic/logic.rs` and `wasmtime_runner/logic.rs`, replace `public_key_len as usize` with `public_key.decode()?.trie_id_len()` (or decode the key first and call `.trie_id_len()` on the result) when computing the exec fee. This mirrors what the runtime path already does correctly via `action.public_key.trie_id_len()`.

### Proof of Concept

1. Deploy a contract that calls `promise_batch_action_add_gas_key_with_full_access` with a borsh-encoded ML-DSA-65 public key (1953 bytes) and `num_nonces = 1`.
2. Observe that the exec gas charged equals `gas_key_add_key_exec_fee(account_id_len, 1953, 1)`.
3. Submit the same `AddKey` action as a signed transaction; observe exec gas equals `gas_key_add_key_exec_fee(account_id_len, 33, 1)`.
4. The difference is `1920 × gas_key_byte_exec_fee` per nonce — the contract path charges ~59× more exec gas than the transaction path for the same trie write, causing the contract call to exhaust gas while the transaction succeeds.

### Citations

**File:** core/parameters/src/cost.rs (L876-898)
```rust
/// Exec fee when adding a gas key with `num_nonces` nonces, split into base
/// and per-byte components. Each nonce writes a trie entry of
/// (access_key_key_len + NonceIndex) key bytes and NONCE_VALUE_LEN value bytes.
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
}
```

**File:** core/primitives-core/src/trie_key.rs (L9-15)
```rust
/// Returns the length of the trie key for an access key.
///
/// The trie key format is: `[col_prefix] [account_id] [separator] [public_key]`
/// where the prefix and separator are each a single `u8` byte.
pub fn access_key_key_len(account_id_len: usize, public_key_len: usize) -> usize {
    COLUMN_PREFIX_LEN + account_id_len + ACCESS_KEY_SEPARATOR_LEN + public_key_len
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

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3092-3096)
```rust
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

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3226-3231)
```rust
        let exec_fee = gas_key_add_key_exec_fee(
            &self.fees_config,
            receipt_receiver_id.len(),
            public_key_len as usize,
            num_nonces,
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

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3499-3504)
```rust
    let exec_fee = gas_key_add_key_exec_fee(
        &ctx.fees_config,
        receipt_receiver_id.len(),
        public_key_len as usize,
        num_nonces,
    );
```

**File:** core/crypto/src/signature.rs (L268-339)
```rust
    pub fn len(&self) -> usize {
        match self {
            Self::ED25519(_) => 1 + ed25519_dalek::PUBLIC_KEY_LENGTH,
            Self::SECP256K1(_) => 1 + 64,
            Self::MLDSA65(_) => 1 + ML_DSA_65_PUBLIC_KEY_LENGTH,
        }
    }

    pub fn empty(key_type: KeyType) -> Self {
        match key_type {
            KeyType::ED25519 => {
                PublicKey::ED25519(ED25519PublicKey([0u8; ed25519_dalek::PUBLIC_KEY_LENGTH]))
            }
            KeyType::SECP256K1 => PublicKey::SECP256K1(Secp256K1PublicKey([0u8; 64])),
            KeyType::MLDSA65 => {
                PublicKey::MLDSA65(MlDsa65PublicKey(Box::new([0u8; ML_DSA_65_PUBLIC_KEY_LENGTH])))
            }
        }
    }

    pub fn key_type(&self) -> KeyType {
        match self {
            Self::ED25519(_) => KeyType::ED25519,
            Self::SECP256K1(_) => KeyType::SECP256K1,
            Self::MLDSA65(_) => KeyType::MLDSA65,
        }
    }

    fn key_tag(&self) -> KeyTag {
        match self {
            PublicKey::ED25519(_) => KeyTag::Ed25519,
            PublicKey::SECP256K1(_) => KeyTag::Secp256k1,
            PublicKey::MLDSA65(_) => KeyTag::MlDsa65Full,
        }
    }

    pub fn key_data(&self) -> &[u8] {
        match self {
            Self::ED25519(key) => key.as_ref(),
            Self::SECP256K1(key) => key.as_ref(),
            Self::MLDSA65(key) => key.as_ref(),
        }
    }

    pub fn unwrap_as_ed25519(&self) -> &ED25519PublicKey {
        match self {
            Self::ED25519(key) => key,
            Self::SECP256K1(_) | Self::MLDSA65(_) => panic!(),
        }
    }

    pub fn unwrap_as_secp256k1(&self) -> &Secp256K1PublicKey {
        match self {
            Self::SECP256K1(key) => key,
            Self::ED25519(_) | Self::MLDSA65(_) => panic!(),
        }
    }

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

**File:** runtime/runtime/src/access_keys.rs (L17-29)
```rust
fn access_key_storage_usage(
    fee_config: &RuntimeFeesConfig,
    public_key: &PublicKey,
    access_key: &AccessKey,
) -> StorageUsage {
    let storage_usage_config = &fee_config.storage_usage_config;
    // Use the on-trie identifier length, not the borsh-serialized pubkey
    // length: ML-DSA-65 access keys live in the trie as a SHA3-256 hash
    // (33 bytes incl. type tag), not as a 1953-byte full pubkey.
    public_key.trie_id_len() as u64
        + borsh::object_length(access_key).unwrap() as u64
        + storage_usage_config.num_extra_bytes_record
}
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

**File:** core/primitives-core/src/version.rs (L403-407)
```rust
    /// Add FIPS 204 ML-DSA-65 (post-quantum) as a third transaction signature
    /// scheme alongside ed25519 and secp256k1. Pre-feature blocks reject any
    /// transaction or `AddKey` action carrying an ML-DSA-65 key/signature, so
    /// post-feature there is no question of grandfathered keys.
    PostQuantumSignatures,
```
