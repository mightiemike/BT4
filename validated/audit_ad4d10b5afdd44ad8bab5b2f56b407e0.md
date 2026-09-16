## Finding: Unauthorized "bootstrap" declare bypasses fee, nonce, and signature checks

The reported Solidity bug (`setCluster()` missing `onlyOwner`) is a case of a state-critical operation being gated only by a value an attacker can freely supply, with no real authorization check. The sequencer has an analogous pattern in the **declare-transaction "bootstrap" fast-path**, where the sole "authorization" for a fee-free, signature-free, un-validated class declaration is a magic sender-address constant that any unprivileged transaction sender can put in a self-crafted transaction.

### Title
Unauthenticated "bootstrap declare" fast-path lets any sender declare classes for free without validation - (File: `crates/starknet_api/src/executable_transaction.rs`)

### Summary
`DeclareTransaction::is_bootstrap_declare` treats a transaction as a privileged, genesis-only "bootstrap declare" purely based on attacker-controlled transaction fields — `sender_address == bootstrap_address()`, `nonce == 0`, `version == 3`, and a zero max possible fee — with no cryptographic or protocol-level restriction tying this path to actual genesis/bootstrap conditions. [1](#0-0) 

### Finding Description
The bootstrap declare path is checked and executed in `AccountTransaction::execute_raw`: if `tx.is_bootstrap_declare(charge_fee)` returns true, `__validate_declare__` is skipped entirely and fee/nonce handling is bypassed, going straight to `run_execute`, which declares the class. [2](#0-1) 

The same bypass exists at the Starknet OS level: when `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and the computed `max_possible_fee == 0`, the OS skips `__validate_declare__` and fee charging and directly writes the class hash into `contract_class_changes`. [3](#0-2) 

Critically, `bootstrap_address()` is a **public, hardcoded constant** (`ContractAddress::from(0x424f4f545354524150_u128)`, the felt representation of `"BOOTSTRAP"`), not a secret key or governance-verified identity. [4](#0-3) 

At the gateway, the only access-control check applied to declare transactions is `check_declare_permissions`, which consults `authorized_declarer_accounts`. This allowlist defaults to `None`, and `is_authorized_declarer` returns `true` for **every address** when the allowlist is unset (which is the default deployment configuration). [5](#0-4) [6](#0-5) [7](#0-6) 

There is no other check anywhere in the gateway's stateless or stateful validators that restricts who may set `sender_address` to the bootstrap constant, that requires this to occur only at genesis (e.g., a block-number gate), or that requires any signature/ownership proof for this path — the entire point of the bootstrap flow is that `__validate_declare__` and fee charging are skipped. The test helper `generate_bootstrap_declare()` demonstrates that constructing such a transaction requires nothing more than setting `sender_address`, `nonce=0`, and zero-fee resource bounds — no private key, no deployed account, no signature. [8](#0-7) 

The only thing preventing indefinite abuse of a *specific* class hash is the OS's `prev_value=0` enforcement on `dict_update`, which merely prevents redeclaring the *same* class hash twice — it does not prevent an attacker from repeatedly declaring **new, distinct** class hashes for free, indefinitely, by simply crafting new bootstrap-shaped declare transactions.

### Impact Explanation
Any unprivileged transaction sender can submit an unlimited number of declare transactions impersonating the "BOOTSTRAP" identity (for as-yet-undeclared class hashes) to:
- Bypass the `__validate_declare__` account-authorization step entirely (no signature check).
- Bypass all fee charging (`charge_fee` false / `max_possible_fee == 0`), consuming sequencer/committer resources (Sierra→CASM compilation, state writes, Patricia tree updates) for free.
- Because the tx nonce is never incremented for this path, the mempool does not remove it after inclusion and will keep re-proposing it, causing repeated re-processing until it is rejected on redeclaration — an amplification vector.

This is an unauthorized account action (using a privileged fee-free/validation-free class-declaration channel without any real authorization) and a free/uncosted path into the canonical class registry, which the committer/Patricia tree must persist as part of the committed state root — i.e., it corrupts the intended trust model that "declare" always costs fees and requires the declaring account's authorization.

### Likelihood Explanation
High. The only "gate" is a hardcoded, public constant compared against a self-supplied transaction field; the default gateway configuration (`authorized_declarer_accounts` unset) does not restrict who can use it, and constructing a qualifying transaction requires no privileged information — the test utility `generate_bootstrap_declare()` shows this is trivial to build.

### Recommendation
Do not gate the bootstrap/no-fee/no-validation declare path solely on a transaction-supplied `sender_address` value. Restrict this path so it can only be exercised during actual genesis/bootstrap (e.g., gate it on chain state — such as block number 0 / empty state — rather than solely on transaction fields), or remove the fee/validation bypass once genesis has completed, and ensure `authorized_declarer_accounts` (or an equivalent hard genesis-only check) is enforced by default rather than defaulting to "allow all."

### Proof of Concept
1. Craft an `RpcDeclareTransaction::V3` with:
   - `sender_address = 0x424f4f545354524150` (`bootstrap_address()`)
   - `nonce = 0`
   - zero-fee `resource_bounds` (as in `generate_bootstrap_declare()`)
   - any not-yet-declared `contract_class` / matching `compiled_class_hash`
   - no valid signature required (empty signature accepted, since `__validate_declare__` is skipped)
2. Submit via the gateway's `add_tx`. `check_declare_permissions` passes because `authorized_declarer_accounts` is unset by default.
3. `AccountTransaction::execute_raw` detects `is_bootstrap_declare(charge_fee=false)` is true and executes `run_execute` directly, declaring the class with no fee charged and no `__validate_declare__` call.
4. Repeat with new class hashes indefinitely to declare arbitrary classes for free without ever controlling a real account or private key.

### Citations

**File:** crates/starknet_api/src/executable_transaction.rs (L246-263)
```rust
    // Returns whether the declare transaction is for bootstrapping.
    // In this case, no account-related actions should be made besides the declaration.
    pub fn is_bootstrap_declare(&self, charge_fee: bool) -> bool {
        if let crate::transaction::DeclareTransaction::V3(tx) = &self.tx {
            return tx.sender_address == Self::bootstrap_address()
                && tx.nonce == Nonce(Felt::ZERO)
                && !charge_fee;
        }
        false
    }

    /// Returns the address of the bootstrap contract.
    /// Declare transactions can be sent from this contract with no validation, fee or nonce
    /// change. This is used for starting a new Starknet system.
    pub fn bootstrap_address() -> ContractAddress {
        // A felt representation of the string 'BOOTSTRAP'.
        ContractAddress::from(0x424f4f545354524150_u128)
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L888-912)
```rust
        // Do not run validate or perform any account-related actions for declare transactions that
        // meet the following conditions.
        // This flow is used for the sequencer to bootstrap a new system.
        // Note: The absence of any account-related action leads to some unintuitive but expected
        // behavior:
        // - After the transaction is executed successfully, the batcher does not notify the mempool
        //   about its inclusion in a block. As a result, the transaction remains in the mempool.
        // - When the next block is produced, the mempool will propose the same transaction again.
        // - This time, execution will fail because the contract has already been declared.
        // - The transaction will then be marked as rejected, the mempool will be notified, and the
        //   transaction will be removed from the mempool.
        if let Transaction::Declare(tx) = &self.tx {
            if tx.is_bootstrap_declare(self.execution_flags.charge_fee) {
                let mut context = EntryPointExecutionContext::new_invoke(
                    tx_context.clone(),
                    self.execution_flags.charge_fee,
                    SierraGasRevertTracker::new(GasAmount::default()),
                );
                let mut remaining_gas = 0;
                let res = tx.run_execute(state, &mut context, &mut remaining_gas)?;
                assert!(res.is_none(), "Declare execute should not result in a CallInfo.");

                return Ok(TransactionExecutionInfo::default());
            }
        }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L761-776)
```text
    // Do not run validate or perform any account-related actions for declare transactions that
    // meet the following conditions.
    // This flow is used for the sequencer to bootstrap a new system.
    if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
        let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
        if (max_possible_fee == 0) {
            // Declare the class hash and skip the rest of the transaction.
            // Note that prev_value=0 enforces that a class may be declared only once.
            assert_not_zero(compiled_class_hash);
            dict_update{dict_ptr=contract_class_changes}(
                key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
            );
            %{ SkipTx %}
            return ();
        }
    }
```

**File:** crates/apollo_gateway/src/gateway.rs (L407-433)
```rust
    fn check_declare_permissions(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> Result<(), StarknetError> {
        // TODO(noamsp): Return same error as in Python gateway.
        if self.config.static_config.block_declare {
            return Err(StarknetError {
                code: StarknetErrorCode::UnknownErrorCode(
                    "StarknetErrorCode.BLOCKED_TRANSACTION_TYPE".to_string(),
                ),
                message: "Transaction type is temporarily blocked.".to_string(),
            });
        }
        let RpcDeclareTransaction::V3(declare_v3_tx) = declare_tx;
        if !self.config.is_authorized_declarer(&declare_v3_tx.sender_address) {
            return Err(StarknetError {
                code: StarknetErrorCode::KnownErrorCode(
                    KnownStarknetErrorCode::UnauthorizedDeclare,
                ),
                message: format!(
                    "Account address {} is not allowed to declare contracts.",
                    &declare_v3_tx.sender_address
                ),
            });
        }
        Ok(())
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L140-146)
```rust
impl GatewayConfig {
    pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
        match &self.static_config.authorized_declarer_accounts {
            Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
            None => true,
        }
    }
```

**File:** crates/apollo_deployments/resources/app_configs/gateway_config.json (L1-4)
```json
{
  "gateway_config.static_config.authorized_declarer_accounts": "",
  "gateway_config.static_config.authorized_declarer_accounts.#is_none": true,
  "gateway_config.static_config.block_declare": false,
```

**File:** crates/mempool_test_utils/src/starknet_api_test_utils.rs (L585-595)
```rust
/// Generate a declare transaction for initial bootstrapping phase (no fees).
pub fn generate_bootstrap_declare() -> RpcTransaction {
    let bootstrap_declare_args = declare_tx_args!(
        signature: TransactionSignature::default(),
        sender_address: DeclareTransaction::bootstrap_address(),
        resource_bounds: ValidResourceBounds::create_for_testing_no_fee_enforcement(),
        nonce: Nonce(Felt::ZERO),
        compiled_class_hash: *COMPILED_CLASS_HASH,
    );
    rpc_declare_tx(bootstrap_declare_args, contract_class())
}
```
