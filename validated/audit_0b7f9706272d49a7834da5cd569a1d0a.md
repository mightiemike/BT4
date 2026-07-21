### Title
`py_deploy_account` binds `contract_address` from caller-supplied `sender_address` instead of computing it from transaction fields — (File: `crates/native_blockifier/src/py_deploy_account.rs`)

---

### Summary

In `py_deploy_account`, the `contract_address` field of the executable `DeployAccountTransaction` is populated by reading the Python object's `sender_address` attribute verbatim, rather than being derived from the transaction's cryptographic fields (`class_hash`, `contract_address_salt`, `constructor_calldata`). Every other code path in the codebase computes this address canonically. The Python-to-Rust conversion boundary silently accepts any address the Python layer supplies, breaking the invariant that `contract_address` must equal the Pedersen-hash-derived deployment address.

---

### Finding Description

`py_deploy_account` in `crates/native_blockifier/src/py_deploy_account.rs` constructs an executable `DeployAccountTransaction` as follows:

```rust
// line 101-104
let tx_hash = TransactionHash(py_attr::<PyFelt>(py_tx, "hash_value")?.0);
let contract_address =
    ContractAddress::try_from(py_attr::<PyFelt>(py_tx, "sender_address")?.0)?;
Ok(DeployAccountTransaction { tx, tx_hash, contract_address })
```

Both `tx_hash` and `contract_address` are taken from the Python object's fields without any cryptographic verification. The `contract_address` is read from `sender_address` — a field whose value is entirely controlled by the Python caller.

Every other production code path derives `contract_address` from the transaction fields:

- `DeployAccountTransaction::create()` in `crates/starknet_api/src/executable_transaction.rs` line 327:
  ```rust
  let contract_address = deploy_account_tx.calculate_contract_address()?;
  ```
- `convert_rpc_tx_to_internal` in `crates/apollo_transaction_converter/src/transaction_converter.rs` line 379:
  ```rust
  let contract_address = tx.calculate_contract_address()?;
  ```
- `executable_deploy_account_tx` in `crates/starknet_api/src/test_utils/deploy_account.rs` line 128:
  ```rust
  let contract_address = tx.calculate_contract_address().unwrap();
  ```

The canonical formula (`calculate_contract_address(salt, class_hash, constructor_calldata, ZERO_DEPLOYER)`) is defined in `crates/starknet_api/src/transaction.rs` lines 459–473.

The resulting `contract_address` field is then used in two critical places in `crates/blockifier/src/transaction/transactions.rs`:

- Line 249: `storage_address: self.contract_address()` — the address at which the constructor is executed and state is written.
- Line 271: `sender_address: self.contract_address()` — the `sender_address` placed into `TransactionInfo`, which is visible to the contract's `__validate_deploy__` and `__constructor__` entry points.

The function is called from `py_tx` in `crates/native_blockifier/src/py_transaction.rs` line 150, which is invoked by:
- `PyBlockExecutor::execute()` (line 167) — production block execution path used by the Python OS
- `PyBlockExecutor::execute_txs()` (line 187) — batch execution path
- `PyValidator::perform_validations()` — stateful validation path used by the gateway

If the Python layer supplies a `sender_address` that diverges from the cryptographically derived address, the blockifier executes the constructor at the wrong address, writes state to the wrong storage slot, and exposes the wrong `sender_address` to the contract's execution context — all without any error.

---

### Impact Explanation

If the Python OS or gateway provides a `sender_address` that does not match the canonical `calculate_contract_address(...)` result:

1. **Wrong state**: The constructor is executed at `sender_address` (line 249, `storage_address: self.contract_address()`), writing class hash and storage to the wrong address. The cryptographically correct address remains undeployed.
2. **Wrong execution context**: `TransactionInfo.sender_address` (line 271) is set to the wrong address, so `get_execution_info()` inside the constructor returns a wrong `account_contract_address`.
3. **Wrong receipt/event**: `tx_hash` is also taken from `hash_value` without re-derivation (line 101), so the emitted receipt and events carry an unverified hash.
4. **Wrong validation**: In the `PyValidator` path, nonce and fee-balance checks are performed against the wrong address.

This matches: **Critical — Wrong state, receipt, event, class hash, or storage value from blockifier/syscall/execution logic for accepted input.**

---

### Likelihood Explanation

The `native_blockifier` crate is the Python-to-Rust bridge used by the Starknet OS and the Python gateway. The Python OS constructs the `sender_address` field from the transaction body before passing it to `py_deploy_account`. Because the Rust side performs no verification, any divergence — whether from a Python OS bug, a version mismatch in the address-derivation formula between the Python and Rust layers, or a malicious Python-side input — is silently accepted and propagated into execution. The `PyValidator` path is particularly sensitive because it is on the critical path for gateway admission of user-submitted `DeployAccount` transactions.

---

### Recommendation

Replace the `sender_address` read with a canonical derivation:

```rust
// Instead of:
let contract_address =
    ContractAddress::try_from(py_attr::<PyFelt>(py_tx, "sender_address")?.0)?;

// Use:
let contract_address = tx.calculate_contract_address()?;
```

Optionally, assert that the Python-supplied `sender_address` matches the computed value to surface mismatches as hard errors rather than silent divergences. Similarly, `tx_hash` should be re-derived from `tx` and the node's `chain_id` rather than trusted from `hash_value`.

---

### Proof of Concept

1. Construct a Python `DeployAccount` transaction object with valid `class_hash`, `contract_address_salt`, and `constructor_calldata` fields, but set `sender_address` to an arbitrary address `A` that does not equal `calculate_contract_address(salt, class_hash, calldata, 0)`.
2. Pass this object to `PyBlockExecutor::execute()` or `PyValidator::perform_validations()`.
3. `py_deploy_account` (line 102–103) reads `A` from `sender_address` without verification and constructs `DeployAccountTransaction { tx, tx_hash, contract_address: A }`.
4. `run_execute` (line 249) sets `storage_address: A` and executes the constructor there; the canonical address is never touched.
5. `create_tx_info` (line 271) sets `sender_address: A` in `TransactionInfo`, so the contract's `get_execution_info()` returns `A` as `account_contract_address`.
6. State diff shows class hash written at `A`; the correct deployment address has no class hash — the deployed account is effectively at the wrong address, unreachable by the user who submitted the transaction.