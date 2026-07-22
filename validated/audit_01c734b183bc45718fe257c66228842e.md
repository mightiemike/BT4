### Title
Python-Supplied `contract_address` and `tx_hash` Accepted Without Recomputation in `py_deploy_account` — (File: `crates/native_blockifier/src/py_deploy_account.rs`)

---

### Summary

`py_deploy_account` constructs an executable `DeployAccountTransaction` by blindly trusting two critical fields — `contract_address` (read from `sender_address`) and `tx_hash` (read from `hash_value`) — directly from the Python-supplied object, without recomputing either from the transaction's canonical parameters. This is the sequencer-native analog of the `Hypervisor.deposit` bug: just as that function let the caller freely set `from` and `to`, this conversion lets the Python layer freely set the deployed contract's address and the hash used for signature verification.

---

### Finding Description

In `py_deploy_account` (lines 101–104):

```rust
let tx_hash = TransactionHash(py_attr::<PyFelt>(py_tx, "hash_value")?.0);
let contract_address =
    ContractAddress::try_from(py_attr::<PyFelt>(py_tx, "sender_address")?.0)?;
Ok(DeployAccountTransaction { tx, tx_hash, contract_address })
```

Both fields are taken verbatim from the Python object. No verification is performed that:
- `contract_address` equals `calculate_contract_address(salt, class_hash, constructor_calldata, DEPLOYER_ZERO)`, and
- `tx_hash` equals the Poseidon/Pedersen hash of the canonical transaction fields over the correct `chain_id`.

The safe constructor `DeployAccountTransaction::create` (lines 323–331 of `crates/starknet_api/src/executable_transaction.rs`) exists precisely to enforce these invariants — it calls `calculate_contract_address()` and `calculate_transaction_hash()` — but `py_deploy_account` bypasses it entirely.

The Python object is produced by the Starknet OS Python layer and fed into the Rust blockifier via `py_tx` → `py_deploy_account` → `AccountTransaction::new_for_sequencing` (lines 149–151 of `crates/native_blockifier/src/py_transaction.rs`). The Python layer reads transactions from external sources (feeder gateway during central sync, or consensus messages during P2P sync). A malicious or compromised feeder gateway can supply a `sender_address` that diverges from the deterministic contract address, and a `hash_value` that diverges from the canonical hash.

---

### Impact Explanation

**`contract_address` divergence → wrong state committed:**

`DeployAccountTransaction::run_execute` uses `self.contract_address()` as the `storage_address` for the constructor call:

```rust
let constructor_context = ConstructorContext {
    class_hash,
    code_address: None,
    storage_address: self.contract_address(),   // ← attacker-controlled
    caller_address: ContractAddress::default(),
};
```

If `contract_address` is forged, the constructor runs against the wrong storage slot, deploying the class at an address that does not match the deterministic formula. The resulting state diff records the class hash and storage writes at the wrong address. This is a **wrong state / wrong storage value** committed by the blockifier.

**`tx_hash` divergence → wrong signature preimage:**

`DeployAccountTransaction::create_tx_info` places `tx_hash` into `CommonAccountFields::transaction_hash`, which is the value exposed to the account contract's `__validate_deploy__` entry point as `get_tx_info().transaction_hash`. If the hash is forged, the account contract's signature check runs against the wrong preimage, potentially accepting an invalid signature or binding the wrong signer.

---

### Impact Explanation (Scope Match)

- **Critical — Wrong state, receipt, event, storage value from blockifier/syscall/execution logic for accepted input**: the constructor is executed at the wrong `storage_address`, producing a wrong state diff.
- **Critical — Invalid or unauthorized Starknet transaction accepted through account validation / signature logic**: a forged `tx_hash` causes `__validate_deploy__` to verify a signature over the wrong hash.

---

### Likelihood Explanation

The trigger path is: external feeder gateway (central sync) or P2P peer (consensus sync) → Python OS layer → `py_deploy_account` → blockifier execution. A malicious feeder gateway or a compromised consensus peer can supply arbitrary `sender_address` and `hash_value` fields. The Python layer performs no independent recomputation before passing these to Rust. The `native_blockifier` is a production component used in the sequencer's block re-execution and proving pipeline.

---

### Recommendation

Replace the direct field reads with recomputation, mirroring `DeployAccountTransaction::create`:

```rust
// Instead of:
let tx_hash = TransactionHash(py_attr::<PyFelt>(py_tx, "hash_value")?.0);
let contract_address =
    ContractAddress::try_from(py_attr::<PyFelt>(py_tx, "sender_address")?.0)?;

// Use:
let contract_address = tx.calculate_contract_address()?;
let tx_hash = tx.calculate_transaction_hash(chain_id, &tx.version())?;
```

This requires threading `chain_id` into `py_deploy_account`, consistent with how `TransactionConverter` already carries `chain_id` for the same purpose. Alternatively, call `DeployAccountTransaction::create(tx, chain_id)` directly and discard the Python-supplied `hash_value` and `sender_address`.

---

### Proof of Concept

1. A malicious feeder gateway serves a `DEPLOY_ACCOUNT` transaction JSON where `sender_address` = `0xdeadbeef` (an arbitrary address) and `hash_value` = `0xbadc0de` (an arbitrary hash), while `class_hash`, `contract_address_salt`, and `constructor_calldata` are valid.
2. The Python OS layer deserializes this and passes the object to `py_deploy_account`.
3. `py_deploy_account` reads `sender_address` → `contract_address = 0xdeadbeef` and `hash_value` → `tx_hash = 0xbadc0de` without recomputation.
4. The blockifier executes the constructor at `storage_address = 0xdeadbeef`, writing the class hash and constructor storage to that slot.
5. The state diff records the deployment at `0xdeadbeef` instead of the canonical address `calculate_contract_address(salt, class_hash, calldata, 0)`.
6. The `__validate_deploy__` entry point receives `tx_hash = 0xbadc0de` and verifies the signature against it — a hash the account owner never signed.

**Divergent value**: `contract_address` stored in the blockifier state = attacker-supplied felt vs. `calculate_contract_address(...)` = canonical deterministic address. These are different felts whenever the attacker supplies any value other than the canonical one. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** crates/native_blockifier/src/py_deploy_account.rs (L101-104)
```rust
    let tx_hash = TransactionHash(py_attr::<PyFelt>(py_tx, "hash_value")?.0);
    let contract_address =
        ContractAddress::try_from(py_attr::<PyFelt>(py_tx, "sender_address")?.0)?;
    Ok(DeployAccountTransaction { tx, tx_hash, contract_address })
```

**File:** crates/starknet_api/src/executable_transaction.rs (L323-331)
```rust
    pub fn create(
        deploy_account_tx: crate::transaction::DeployAccountTransaction,
        chain_id: &ChainId,
    ) -> Result<Self, StarknetApiError> {
        let contract_address = deploy_account_tx.calculate_contract_address()?;
        let tx_hash =
            deploy_account_tx.calculate_transaction_hash(chain_id, &deploy_account_tx.version())?;
        Ok(Self { tx: deploy_account_tx, tx_hash, contract_address })
    }
```

**File:** crates/native_blockifier/src/py_transaction.rs (L149-151)
```rust
        TransactionType::DeployAccount => {
            let tx = ExecutableTransaction::DeployAccount(py_deploy_account(tx)?);
            AccountTransaction::new_for_sequencing(tx).into()
```

**File:** crates/blockifier/src/transaction/transactions.rs (L244-260)
```rust
    ) -> TransactionExecutionResult<Option<CallInfo>> {
        let class_hash = self.class_hash();
        let constructor_context = ConstructorContext {
            class_hash,
            code_address: None,
            storage_address: self.contract_address(),
            caller_address: ContractAddress::default(),
        };
        let call_info = execute_deployment(
            state,
            context,
            constructor_context,
            self.constructor_calldata(),
            remaining_gas,
        )?;

        Ok(Some(call_info))
```

**File:** crates/blockifier/src/transaction/transactions.rs (L264-295)
```rust
impl TransactionInfoCreatorInner for DeployAccountTransaction {
    fn create_tx_info(&self, only_query: bool) -> TransactionInfo {
        let common_fields = CommonAccountFields {
            transaction_hash: self.tx_hash(),
            version: self.version(),
            signature: self.signature(),
            nonce: self.nonce(),
            sender_address: self.contract_address(),
            only_query,
        };

        match &self.tx {
            starknet_api::transaction::DeployAccountTransaction::V1(tx) => {
                TransactionInfo::Deprecated(DeprecatedTransactionInfo {
                    common_fields,
                    max_fee: tx.max_fee,
                })
            }
            starknet_api::transaction::DeployAccountTransaction::V3(tx) => {
                TransactionInfo::Current(CurrentTransactionInfo {
                    common_fields,
                    resource_bounds: tx.resource_bounds,
                    tip: tx.tip,
                    nonce_data_availability_mode: tx.nonce_data_availability_mode,
                    fee_data_availability_mode: tx.fee_data_availability_mode,
                    paymaster_data: tx.paymaster_data.clone(),
                    account_deployment_data: AccountDeploymentData::default(),
                    proof_facts: ProofFacts::default(),
                })
            }
        }
    }
```
