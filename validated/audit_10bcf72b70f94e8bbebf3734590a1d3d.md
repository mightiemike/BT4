### Title
`TransferToGasKey`/`WithdrawFromGasKey` send-fee uses trie-id length (33 B) instead of wire length (1953 B) for ML-DSA-65 keys in the transaction path, creating a ~59× fee divergence from the host-function path — (`runtime/runtime/src/config.rs`)

---

### Summary

`total_send_fees` in `runtime/runtime/src/config.rs` computes the `TransferToGasKey` send fee using `action.public_key.trie_id_len()` (33 bytes for ML-DSA-65), while the host-function path in `runtime/near-vm-runner/src/logic/logic.rs` uses the raw `public_key_len` passed by the contract (1953 bytes for ML-DSA-65). The `gas_key_transfer_send_fee` function's own documentation states the fee is "Based on the public key length (what the sender sees)" — i.e., the wire format. Symmetrically, `exec_fee` correctly uses `trie_id_len()` = 33 bytes for the exec fee, but the host-function path uses 1953 bytes there too. The result is a ~59× underpricing of the send fee for `TransferToGasKey` transactions with ML-DSA-65 keys, and a ~59× overpricing of the exec fee for contract-emitted `TransferToGasKey` actions with ML-DSA-65 keys.

---

### Finding Description

**Two divergent length values for the same action:**

`gas_key_transfer_send_fee` is documented as:

> "Send fee for TransferToGasKey / WithdrawFromGasKey actions. Based on the public key length (what the sender sees)." [1](#0-0) 

"What the sender sees" is the wire format. For ML-DSA-65, `PublicKey::len()` = 1 + 1952 = **1953 bytes**. For ed25519/secp256k1, `len()` == `trie_id_len()`, so there is no divergence for those key types.

**Transaction path (undercharges send fee):**

`total_send_fees` passes `action.public_key.trie_id_len()` = **33 bytes** for ML-DSA-65: [2](#0-1) 

**Host-function path (correct send fee, overcharges exec fee):**

`promise_batch_action_transfer_to_gas_key` passes the raw `public_key_len` = **1953 bytes** for both send and exec fees: [3](#0-2) 

**Exec fee is correct in the transaction path but wrong in the host-function path:**

`exec_fee` correctly uses `trie_id_len()` = 33 bytes (the trie key length the receiver reads/writes): [4](#0-3) 

The exec fee comment says "Based on the access key trie key length + estimated value length (what the receiver needs to read/write in the trie)." The trie key for ML-DSA-65 is 33 bytes (SHA3-256 hash), not 1953 bytes. The host-function path passes 1953 bytes here too, overcharging exec fee by ~59×.

**The exact divergent values:**

| Path | Send fee `public_key_len` | Exec fee `public_key_len` |
|---|---|---|
| Transaction (`total_send_fees` / `exec_fee`) | 33 B (wrong — should be 1953) | 33 B (correct) |
| Host function (`promise_batch_action_transfer_to_gas_key`) | 1953 B (correct) | 1953 B (wrong — should be 33) |

The `trie_id_len()` / `len()` split was introduced specifically for ML-DSA-65: [5](#0-4) 

The documentation explicitly warns: "Callers that still use `len()` for trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes." The inverse error — using `trie_id_len()` where `len()` is required — is present in `total_send_fees`. [6](#0-5) 

---

### Impact Explanation

**Send-fee underpricing (transaction path):** A user submitting a `TransferToGasKey` transaction with an ML-DSA-65 gas key pays the per-byte send fee on 33 bytes instead of 1953 bytes — a ~59× reduction in the per-byte component. If `gas_key_byte` send fee is calibrated for the wire length, this enables spam/DoS of the gas-key transfer mechanism at a fraction of the intended cost.

**Exec-fee overpricing (host-function path):** A contract emitting `TransferToGasKey` via `promise_batch_action_transfer_to_gas_key` pays the per-byte exec fee on 1953 bytes instead of 33 bytes — a ~59× overcharge. This makes the action economically unusable from contracts with ML-DSA-65 gas keys, breaking the feature's contract-callable path.

Both effects are user-controlled: any account holding an ML-DSA-65 gas key can trigger either path.

---

### Likelihood Explanation

Requires both `PostQuantumSignatures` (stabilized at protocol version 85) and `GasKeys` (currently nightly) to be simultaneously active. Once `GasKeys` is stabilized, the divergence becomes reachable on mainnet by any unprivileged user who holds an ML-DSA-65 gas key. The code paths are in production and the divergence is deterministic — it fires on every `TransferToGasKey` or `WithdrawFromGasKey` action with an ML-DSA-65 key.

---

### Recommendation

In `total_send_fees` (`runtime/runtime/src/config.rs`), change the `TransferToGasKey` (and `WithdrawFromGasKey` if present) arm to use `action.public_key.len()` instead of `action.public_key.trie_id_len()` for the send-fee argument, matching the documented intent of `gas_key_transfer_send_fee` ("what the sender sees" = wire format):

```rust
// Before (wrong for ML-DSA-65):
gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.trie_id_len())

// After (correct — wire length):
gas_key_transfer_send_fee(fees, sender_is_receiver, action.public_key.len())
```

In the host-function path (`runtime/near-vm-runner/src/logic/logic.rs`), change the `gas_key_transfer_exec_fee` call to use `trie_id_len()` of the decoded public key instead of the raw `public_key_len`, matching the documented intent of `gas_key_transfer_exec_fee` ("what the receiver needs to read/write in the trie" = trie key length).

Add a test asserting that `total_send_fees` and the host-function path produce identical send fees for the same `TransferToGasKey` action with an ML-DSA-65 key.

---

### Proof of Concept

1. Activate both `PostQuantumSignatures` (v85) and `GasKeys` on a test network.
2. Create an account with an ML-DSA-65 gas key (`AddKey` with `GasKeyFullAccess`).
3. Submit a `TransferToGasKey` transaction referencing the ML-DSA-65 key.
4. Observe the burnt gas for the send-fee component: it will reflect 33 bytes × `gas_key_byte` send rate.
5. Deploy a contract that calls `promise_batch_action_transfer_to_gas_key` with the same ML-DSA-65 key (1953-byte wire encoding).
6. Observe the burnt gas for the exec-fee component: it will reflect 1953 bytes × `gas_key_byte` exec rate.
7. Compare: the transaction send fee is ~59× lower than the host-function send fee; the contract exec fee is ~59× higher than the transaction exec fee. The divergence is exactly `(1953 − 33) × gas_key_byte` = 1920 × `gas_key_byte` in each direction.

### Citations

**File:** core/parameters/src/cost.rs (L814-827)
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

**File:** docs/architecture/how/post_quantum_signatures.md (L189-191)
```markdown
2. **`PublicKey::trie_id_len()` is a new contract** that all
   storage-cost code must respect. Callers that still use `len()` for
   trie-storage costing will misprice ML-DSA-65 keys by ~1900 bytes.
```
