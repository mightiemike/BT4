No vulnerability found for this question.

**Analysis:**

In `TransactionMetadata::new`, both `script_hash` and `script_size` are computed via the same pattern match against `txn.payload().executable_ref()`: [1](#0-0) [2](#0-1) 

Both computations use `if let Ok(TransactionExecutableRef::Script(s)) = txn.payload().executable_ref()`, which only binds `s` when the ref is exactly the `Script` variant; every other case (`EntryFunction`, `Empty`, `Encrypted`, or an `Err` from a deprecated `ModuleBundle` payload) falls through to the `else` branch, yielding `(vec![], false)` for the hash/governance-approval tuple and `NumBytes::zero()` for the size.

`executable_ref()` is not an independent or attacker-influenced field — it's a pure, deterministic projection derived directly from the `TransactionPayload`'s own stored variant: [3](#0-2) 

- `TransactionPayload::Script` → always `Script(_)`
- `TransactionPayload::EntryFunction` → always `EntryFunction(_)`
- `TransactionPayload::Multisig`/`Payload::V1{executable}` → delegates to the enum's own `TransactionExecutable` variant
- `TransactionPayload::EncryptedPayload` → `Encrypted` unless already `Decrypted`, in which case it delegates to the decrypted plaintext's `TransactionExecutable` variant [4](#0-3) 

Even for encrypted payloads, once decrypted the `executable_ref()` reflects the actual verified decrypted executable (checked against `payload_hash` via `try_into_decrypted`, see lines 202-235 of the same file), so a sender cannot get `Script` returned unless the payload genuinely is/decrypts to a `Script`. There is no code path where an unprivileged sender can make `executable_ref()` ambiguously resolve to `Script` for a payload whose actual `TransactionExecutable` is `EntryFunction`, `Empty`, or `Encrypted` — the enum match is exhaustive and one-to-one with the underlying stored data. Consequently `script_hash`/`script_size` cannot be bound to the wrong `code()` bytes, and non-script transactions correctly get zero/empty values with no ability to spoof governance-hash approval or bypass script-size gas accounting.

### Citations

**File:** aptos-move/aptos-vm/src/transaction_metadata.rs (L67-82)
```rust
        let (script_hash, is_approved_gov_script) =
            if let Ok(TransactionExecutableRef::Script(s)) = txn.payload().executable_ref() {
                let script_hash = HashValue::sha3_256_of(s.code()).to_vec();
                let is_approved_gov_script = ApprovedExecutionHashes::fetch_config(resolver)
                    .ok()
                    .flatten()
                    .is_some_and(|approved| {
                        approved
                            .entries
                            .iter()
                            .any(|(_, hash)| hash == &script_hash)
                    });
                (script_hash, is_approved_gov_script)
            } else {
                (vec![], false)
            };
```

**File:** aptos-move/aptos-vm/src/transaction_metadata.rs (L153-159)
```rust
            script_size: if let Ok(TransactionExecutableRef::Script(s)) =
                txn.payload().executable_ref()
            {
                (s.code().len() as u64).into()
            } else {
                NumBytes::zero()
            },
```

**File:** types/src/transaction/mod.rs (L1027-1044)
```rust
    pub fn executable_ref(&self) -> Result<TransactionExecutableRef<'_>> {
        match self {
            TransactionPayload::EntryFunction(entry_function) => {
                Ok(TransactionExecutableRef::EntryFunction(entry_function))
            },
            TransactionPayload::Script(script) => Ok(TransactionExecutableRef::Script(script)),
            TransactionPayload::Multisig(multisig) => Ok(multisig.as_transaction_executable_ref()),
            TransactionPayload::Payload(TransactionPayloadInner::V1 { executable, .. }) => {
                Ok(executable.as_ref())
            },
            TransactionPayload::ModuleBundle(_) => {
                Err(format_err!("ModuleBundle variant is deprecated"))
            },
            TransactionPayload::EncryptedPayload(encrypted_payload) => {
                encrypted_payload.executable_ref()
            },
        }
    }
```

**File:** types/src/transaction/encrypted_payload.rs (L171-178)
```rust
    pub fn executable_ref(&self) -> Result<TransactionExecutableRef<'_>> {
        Ok(match self {
            EncryptedPayload::Encrypted(_) | EncryptedPayload::FailedDecryption { .. } => {
                TransactionExecutableRef::Encrypted
            },
            EncryptedPayload::Decrypted { decrypted, .. } => decrypted.executable().as_ref(),
        })
    }
```
