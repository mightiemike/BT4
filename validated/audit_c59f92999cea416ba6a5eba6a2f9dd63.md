### Title
Gas-Key Host Functions Use Wire Length Instead of Trie-ID Length for ML-DSA-65 Exec-Fee Computation, Breaking Fee-Parity Invariant — (`runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

---

### Summary

After `ProtocolFeature::PostQuantumSignatures` activates, the three gas-key host functions (`promise_batch_action_add_gas_key_with_full_access`, `promise_batch_action_add_gas_key_with_function_call`, `promise_batch_action_transfer_to_gas_key`) pass the raw `public_key_len` argument — the borsh wire length of the public key as supplied by the WASM contract — directly into `gas_key_add_key_exec_fee` / `gas_key_transfer_exec_fee`. For ML-DSA-65 keys the wire length is **1953 bytes**, but the actual on-trie identifier is a 33-byte SHA3-256 hash. The transaction path correctly calls `public_key.trie_id_len()` (= 33). The host-function path does not. This produces a **1920-byte divergence per nonce** in the exec-fee calculation, overcharging the exec gas by ~58× and breaking the documented fee-parity invariant between the two paths.

---

### Finding Description

**Correct path — transaction-level `AddKey` with `GasKeyFullAccess`:**

`permission_exec_fees` in `runtime/runtime/src/config.rs` calls:

```rust
let nonce_fee = gas_key_add_key_exec_fee(
    fees,
    account_id.len(),
    public_key.trie_id_len(),   // 33 for ML-DSA-65
    gas_key_info.num_nonces,
);
``` [1](#0-0) 

**Broken path — host-function `promise_batch_action_add_gas_key_with_full_access`:**

```rust
let exec_fee = gas_key_add_key_exec_fee(
    &self.fees_config,
    receiver_id.len(),
    public_key_len as usize,    // 1953 for ML-DSA-65 (wire length)
    num_nonces,
);
``` [2](#0-1) 

The same substitution appears in the Wasmtime runner: [3](#0-2) 

And in both runners for `promise_batch_action_add_gas_key_with_function_call`: [4](#0-3) [5](#0-4) 

And for `promise_batch_action_transfer_to_gas_key` (exec fee only; the send-fee comment explicitly says "what the sender sees"): [6](#0-5) [7](#0-6) 

**Why the divergence matters:**

`gas_key_add_key_exec_fee` computes the per-nonce trie-write cost as:

```
nonce_key_len = access_key_key_len(account_id_len, public_key_len) + sizeof(NonceIndex)
per_byte      = gas_key_byte × (nonce_key_len + NONCE_VALUE_LEN) × num_nonces
``` [8](#0-7) 

With `public_key_len = 1953` (wire) the nonce key length is inflated by **1920 bytes** relative to the correct 33-byte trie-id. The exec fee is therefore ~58× larger than the actual trie-write cost.

`gas_key_transfer_exec_fee` has the same structure and the same flaw: [9](#0-8) 

**The invariant that is broken:**

The design explicitly requires fee parity between the transaction path and the host-function path, verified by `test_gas_key_fee_parity`. That test only exercises ED25519 keys (where `public_key_len == trie_id_len == 33`), so the ML-DSA-65 divergence is invisible to it. [10](#0-9) 

**The `len()` / `trie_id_len()` split is a documented protocol contract:**

The design document explicitly states that callers using `len()` instead of `trie_id_len()` for trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes: [11](#0-10) 

The host-function path commits exactly this error, using the raw wire length instead of the trie-id length.

---

### Impact Explanation

After `PostQuantumSignatures` activates (protocol version 85), any WASM contract can call `promise_batch_action_add_gas_key_with_full_access` with an ML-DSA-65 public key. The exec gas charged for the resulting action receipt is ~58× the correct amount. Concretely:

- A contract that attaches gas calibrated against the correct 33-byte trie-id cost will have its action receipt fail with gas exhaustion, even though the actual trie write succeeds for the same gas budget when the key is added via a transaction.
- The same overcharge applies to `TransferToGasKey` / `WithdrawFromGasKey` operations on ML-DSA-65 gas keys.
- The fee-parity invariant — that adding a gas key via transaction and via host function costs the same — is broken for ML-DSA-65 keys, making the host-function path functionally unusable for PQ gas keys.

---

### Likelihood Explanation

The trigger condition is `PostQuantumSignatures` enabled (protocol version 85) and a contract calling any of the three affected host functions with an ML-DSA-65 public key. Both conditions are reachable by any unprivileged user after the feature activates. The `test_gas_key_fee_parity` test does not cover ML-DSA-65 keys, so the divergence is not caught by the existing test suite.

---

### Recommendation

Replace `public_key_len as usize` with `public_key.trie_id_len()` in all three host-function exec-fee call sites. The decoded `PublicKey` is already available at that point (as `public_key.decode()?`). Specifically:

1. In `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call` (both `logic.rs` and `wasmtime_runner/logic.rs`): pass `public_key.decode()?.trie_id_len()` to `gas_key_add_key_exec_fee`.
2. In `promise_batch_action_transfer_to_gas_key` (both runners): pass `public_key.decode()?.trie_id_len()` to `gas_key_transfer_exec_fee`.
3. Extend `test_gas_key_fee_parity` to cover `KeyType::MLDSA65` to prevent regression.

---

### Proof of Concept

**Exact divergent values for an ML-DSA-65 key:**

| Quantity | Value |
|---|---|
| `PublicKey::MLDSA65` borsh wire length (`len()`) | 1953 bytes |
| `PublicKey::MLDSA65` on-trie identifier length (`trie_id_len()`) | 33 bytes |
| Divergence per nonce in `nonce_key_len` | 1920 bytes |
| Exec-fee overcharge factor | ~58× | [12](#0-11) 

**Transaction path uses the correct value:** [1](#0-0) 

**Host-function path uses the wrong value (wire length):** [2](#0-1) 

A contract calling `promise_batch_action_add_gas_key_with_full_access` with a 1953-byte ML-DSA-65 borsh-encoded key and `num_nonces = 4` will be charged exec gas for a nonce key of `account_id_len + 1957` bytes instead of `account_id_len + 37` bytes — a 1920-byte inflation per nonce, totalling 7680 extra bytes of `gas_key_byte` charges. The identical `AddKey` action submitted as a transaction is charged for 37-byte nonce keys. The action receipt from the host-function path will exhaust gas at any budget that would have been sufficient for the transaction path.

### Citations

**File:** runtime/runtime/src/config.rs (L389-394)
```rust
    let nonce_fee = gas_key_add_key_exec_fee(
        fees,
        account_id.len(),
        public_key.trie_id_len(),
        gas_key_info.num_nonces,
    );
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

**File:** runtime/near-vm-runner/src/wasmtime_runner/logic.rs (L3324-3325)
```rust
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

**File:** test-loop-tests/src/tests/gas_keys.rs (L1037-1061)
```rust
fn test_gas_key_fee_parity(mode: GasKeyKind) {
    let mut setup = setup_host_function_test();
    let account = setup.account.clone();

    let num_nonces: NonceIndex = 4;
    let gas_key_a_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_tx").into();
    let gas_key_b_signer: Signer =
        InMemorySigner::from_seed(account.clone(), KeyType::ED25519, "gas_key_host_fn").into();

    let public_key_b_base64 = near_primitives_core::serialize::to_base64(
        &borsh::to_vec(&gas_key_b_signer.public_key()).unwrap(),
    );

    // Add gas key A via transaction, B via host function
    let add_a_outcome = setup.run_actions(vec![Action::AddKey(Box::new(AddKeyAction {
        public_key: gas_key_a_signer.public_key(),
        access_key: mode.access_key(num_nonces, &account),
    }))]);
    let add_b_outcome = setup.run_call_promise(serde_json::json!([
        {"batch_create": {"account_id": account.as_str()}, "id": 0},
        mode.add_action_json(num_nonces, &account, &public_key_b_base64),
    ]));
    assert_eq!(add_a_outcome.gas_burnt, add_b_outcome.gas_burnt);
    assert_eq!(add_a_outcome.tokens_burnt, add_b_outcome.tokens_burnt);
```

**File:** docs/architecture/how/post_quantum_signatures.md (L189-191)
```markdown
2. **`PublicKey::trie_id_len()` is a new contract** that all
   storage-cost code must respect. Callers that still use `len()` for
   trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes.
```

**File:** core/crypto/src/signature.rs (L259-339)
```rust
impl PublicKey {
    /// Length of this public key's borsh encoding, in bytes - that is,
    /// the on-the-wire size of the raw key bytes plus a 1-byte borsh
    /// discriminant tag (the leading `+ 1` in each arm).
    ///
    /// For storage-fee accounting use [`PublicKey::trie_id_len`] instead;
    /// for ML-DSA-65 those two diverge (1953 wire vs 33 on-trie).
    // `is_empty` always returns false, so there is no point in adding it
    #[allow(clippy::len_without_is_empty)]
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
