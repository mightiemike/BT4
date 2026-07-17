### Title
Gas-Key Host-Function Fee Calculation Uses Borsh Wire Length Instead of Trie-Storage Length for ML-DSA-65 Keys — (`runtime/near-vm-runner/src/logic/logic.rs`, `runtime/near-vm-runner/src/wasmtime_runner/logic.rs`)

---

### Summary

The `promise_batch_action_transfer_to_gas_key`, `promise_batch_action_add_gas_key_with_full_access`, and `promise_batch_action_add_gas_key_with_function_call` host functions pass the raw contract-supplied `public_key_len` (the borsh wire length, 1953 bytes for ML-DSA-65) to `gas_key_transfer_send_fee`, `gas_key_transfer_exec_fee`, and `gas_key_add_key_exec_fee`. The transaction-path counterparts in `runtime/runtime/src/config.rs` pass `action.public_key.trie_id_len()` (33 bytes for ML-DSA-65) to the same fee helpers. The two lengths diverge by a factor of ~59× for ML-DSA-65 keys, producing a protocol-level fee-domain mismatch: the same on-chain action carries a different gas cost depending on whether it is submitted as a transaction or dispatched from a contract.

---

### Finding Description

`PublicKey::len()` returns the borsh-encoded wire size (1 + 1952 = 1953 bytes for ML-DSA-65), while `PublicKey::trie_id_len()` returns the on-trie identifier size (1 + 32 = 33 bytes for ML-DSA-65, because the trie stores a SHA3-256 hash, not the full key). [1](#0-0) [2](#0-1) 

The fee helpers are explicitly designed to receive the trie-storage length:

- `gas_key_transfer_exec_fee` feeds `public_key_len` into `access_key_key_len(account_id_len, public_key_len)`, which computes the trie key size. [3](#0-2) 
- `gas_key_add_key_exec_fee` feeds `public_key_len` into `access_key_key_len(account_id_len, public_key_len) + size_of::<NonceIndex>()` per nonce. [4](#0-3) 

**Transaction path (correct):** `runtime/runtime/src/config.rs` calls both helpers with `action.public_key.trie_id_len()`: [5](#0-4) [6](#0-5) [7](#0-6) 

**Host-function path (incorrect):** both the wasmer and wasmtime logic pass the raw `public_key_len` argument (the borsh wire length supplied by the contract): [8](#0-7) [9](#0-8) [10](#0-9) [11](#0-10) 

---

### Impact Explanation

For an ML-DSA-65 key (`public_key_len = 1953`, `trie_id_len = 33`):

| Fee component | Transaction path | Host-function path | Ratio |
|---|---|---|---|
| `gas_key_transfer_send_fee` per-byte | `33 × gas_key_byte.send` | `1953 × gas_key_byte.send` | ~59× |
| `gas_key_transfer_exec_fee` per-byte | `access_key_key_len(…,33) × gas_key_byte.exec` | `access_key_key_len(…,1953) × gas_key_byte.exec` | ~59× |
| `gas_key_add_key_exec_fee` per-nonce | `(key_len(…,33)+NonceIndex) × gas_key_byte.exec × N` | `(key_len(…,1953)+NonceIndex) × gas_key_byte.exec × N` | ~59× per nonce |

With `gas_key_byte.exec_fee = 101_435_400` gas/byte and `num_nonces = 65535` (max `u16`), the exec-fee overcharge for `AddGasKey` alone is approximately `1920 × 65535 × 101_435_400 ≈ 12.76 × 10^15` gas — far beyond any per-receipt gas limit — causing every such host-function call to abort with `GasExceeded`. Even for `TransferToGasKey` the ~59× overcharge on the per-byte send component makes the operation economically unviable from contracts.

Both `GasKeys` and `PostQuantumSignatures` activate at protocol version 85, which is below the current stable version 86, so the divergence is live in production. [12](#0-11) 

---

### Likelihood Explanation

Any unprivileged contract deployed after protocol version 85 can call `promise_batch_action_transfer_to_gas_key` or `promise_batch_action_add_gas_key_with_full_access` with an ML-DSA-65 public key. The contract controls `public_key_len` (it is read directly from contract memory), so the divergent value is user-controlled. No privileged role is required. The mismatch is deterministic and reproducible on every node, so it does not cause consensus divergence — but it permanently breaks the gas-cost parity invariant between the transaction and host-function execution paths for ML-DSA-65 gas keys.

---

### Recommendation

Replace `public_key_len as usize` with `public_key.trie_id_len()` in all three host-function call sites after the public key has been decoded:

```rust
// After: let public_key = self.get_public_key(public_key_ptr, public_key_len)?;
let decoded_key = public_key.decode()?;
let key_len_for_fees = decoded_key.trie_id_len();

let send = gas_key_transfer_send_fee(&self.fees_config, sir, key_len_for_fees);
let exec = gas_key_transfer_exec_fee(&self.fees_config, receiver_id.len(), key_len_for_fees);
```

Apply the same fix to `promise_batch_action_add_gas_key_with_full_access` and `promise_batch_action_add_gas_key_with_function_call` in both `logic.rs` and `wasmtime_runner/logic.rs`. This aligns the host-function fee domain with the transaction-path fee domain, restoring the protocol invariant that the same action costs the same gas regardless of execution path.

---

### Proof of Concept

1. Deploy a contract that calls `promise_batch_action_transfer_to_gas_key` with a borsh-encoded ML-DSA-65 public key (1953 bytes).
2. Submit the same `TransferToGasKey` action as a signed transaction with the same ML-DSA-65 key.
3. Compare `gas_burnt` on the action execution receipt:
   - Transaction path: `gas_key_transfer_base.send + 33 × gas_key_byte.send + gas_key_transfer_base.exec + access_key_key_len(…,33) × gas_key_byte.exec`
   - Host-function path: `gas_key_transfer_base.send + 1953 × gas_key_byte.send + gas_key_transfer_base.exec + access_key_key_len(…,1953) × gas_key_byte.exec`

The host-function receipt burns ~59× more gas on the per-byte components despite writing an identical 33-byte trie key. For `AddGasKey` with `num_nonces > 1`, the host-function call aborts with `GasExceeded` while the equivalent transaction succeeds. [13](#0-12) [5](#0-4)

### Citations

**File:** core/crypto/src/signature.rs (L268-273)
```rust
    pub fn len(&self) -> usize {
        match self {
            Self::ED25519(_) => 1 + ed25519_dalek::PUBLIC_KEY_LENGTH,
            Self::SECP256K1(_) => 1 + 64,
            Self::MLDSA65(_) => 1 + ML_DSA_65_PUBLIC_KEY_LENGTH,
        }
```

**File:** core/crypto/src/signature.rs (L333-338)
```rust
    pub fn trie_id_len(&self) -> usize {
        match self {
            Self::ED25519(_) => 1 + ed25519_dalek::PUBLIC_KEY_LENGTH,
            Self::SECP256K1(_) => 1 + 64,
            Self::MLDSA65(_) => 1 + ML_DSA_65_HASH_LENGTH,
        }
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

**File:** runtime/runtime/src/config.rs (L114-116)
```rust
            TransferToGasKey(action) => {
                gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())
                    .total()
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

**File:** runtime/runtime/src/config.rs (L389-394)
```rust
    let nonce_fee = gas_key_add_key_exec_fee(
        fees,
        account_id.len(),
        public_key.trie_id_len(),
        gas_key_info.num_nonces,
    );
```

**File:** runtime/near-vm-runner/src/logic/logic.rs (L3085-3115)
```rust
        let public_key = self.get_public_key(public_key_ptr, public_key_len)?;
        let amount = Balance::from_yoctonear(
            self.memory.get_u128(&mut self.result_state.gas_counter, amount_ptr)?,
        );
        let (receipt_idx, sir) = self.promise_idx_to_receipt_idx_with_sir(promise_idx)?;
        let receiver_id = self.ext.get_receipt_receiver(receipt_idx);
        let send = gas_key_transfer_send_fee(&self.fees_config, sir, public_key_len as usize);
        let exec = gas_key_transfer_exec_fee(
            &self.fees_config,
            receiver_id.len(),
            public_key_len as usize,
        );
        let burn_base = send.base;
        let use_base =
            burn_base.gas.checked_add(exec.base.gas).ok_or(HostError::IntegerOverflow)?;
        self.result_state.gas_counter.pay_action_accumulated(
            burn_base,
            use_base,
            ActionCosts::gas_key_transfer_base,
        )?;
        let burn_byte = send.per_byte;
        let use_byte =
            burn_byte.gas.checked_add(exec.per_byte.gas).ok_or(HostError::IntegerOverflow)?;
        self.result_state.gas_counter.pay_action_accumulated(
            burn_byte,
            use_byte,
            ActionCosts::gas_key_byte,
        )?;
        self.result_state.deduct_balance(amount)?;
        self.ext.append_action_transfer_to_gas_key(receipt_idx, public_key.decode()?, amount);
        Ok(())
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

**File:** core/primitives-core/src/version.rs (L555-571)
```rust
            ProtocolFeature::FixDelegateActionDepositWithFunctionCallError
            | ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
            | ProtocolFeature::FixDelegatedDeterministicStateInit
            | ProtocolFeature::GasKeys
            | ProtocolFeature::ContinuousEpochSync
            | ProtocolFeature::DynamicResharding
            | ProtocolFeature::StickyReshardingValidatorAssignment
            | ProtocolFeature::StrictNonce
            | ProtocolFeature::PostQuantumSignatures
            | ProtocolFeature::UniqueChunkTransactions
            | ProtocolFeature::ValidateBlockOrdinalAndEpochSyncDataHash
            | ProtocolFeature::YieldWithId
            | ProtocolFeature::ExecutionMetadataV4
            | ProtocolFeature::SignedContractCodeResponse
            | ProtocolFeature::ClampOutgoingGasAdmission
            | ProtocolFeature::AccountCostIncrease
            | ProtocolFeature::DelegateV2 => 85,
```
