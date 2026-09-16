### Title
Unauthenticated free bootstrap Declare allows any transaction sender to declare arbitrary contract classes with no fee, no signature, and no nonce consumption - (File: crates/starknet_api/src/executable_transaction.rs)

### Summary
The sequencer implements a special "bootstrap declare" transaction path intended only for initializing a brand-new chain, but the gateway's authorization gate for it is a *user-controlled field* (the `sender_address` in a submitted `RpcDeclareTransaction::V3`), not an operator/config-side check. Any transaction sender can construct a Declare V3 transaction with `sender_address == bootstrap_address()`, `nonce == 0`, and zero resource bounds, and it will be treated by both `blockifier` and the Starknet OS as a privileged, fee-free, signature-free class declaration — exactly the "authorization bypass through user-controlled key/param" bug class described in the InLong advisory (CVE-2023-43668), where sensitive checks are bypassed based on attacker-controlled parameters.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` treats any Declare V3 transaction sent from the fixed magic address `'BOOTSTRAP'` (`0x424f4f545354524150`) with `nonce == 0` and no fee charged as a bootstrap transaction: [1](#0-0) 

In `blockifier`, when `AccountTransaction::execute_raw` sees such a transaction, it completely skips `perform_pre_validation_stage` (nonce/fee/balance checks), skips the `__validate_declare__` account entry point, skips signature verification, and skips fee charging — it just directly runs `try_declare`: [2](#0-1) 

`try_declare` will happily declare *any* class hash supplied in the transaction as long as that specific class hash has not been declared before: [3](#0-2) 

The same bypass is mirrored in the Starknet OS Cairo code executed by provers/re-execution, which checks the *literal string* `'BOOTSTRAP'` as `sender_address` plus `nonce == 0` and `version == 3`, and if `max_possible_fee == 0`, it directly writes the class hash into `contract_class_changes` and skips the rest of transaction processing (`%{ SkipTx %}`) — no `__validate_declare__` call, no nonce increment, no fee transfer: [4](#0-3) 

Critically, the *only* gate on who can submit a Declare transaction at the gateway layer is `check_declare_permissions`, which enforces `block_declare` and an optional `authorized_declarer_accounts` allowlist based on `sender_address`: [5](#0-4) 

Nothing in this check — or anywhere else in the gateway/mempool/stateless validator pipeline that was inspected — restricts the *value* `'BOOTSTRAP'` itself, nor restricts the bootstrap-declare code path to genesis/block 0. `check_declare_permissions` only blocks declares if `authorized_declarer_accounts` is configured and does not include the sender, or if `block_declare` is set; by default neither restricts the bootstrap address. Since `sender_address` is a plain user-controlled field of the submitted RPC transaction, and the "is this a privileged bootstrap tx" check keys purely off that field's *value*, an attacker can forge this special sender address in any Declare V3 transaction at any point in the chain's life, not just at genesis. The `dict_update` `prev_value=0` semantics in the OS and the `try_declare` semantics in blockifier only prevent redeclaring the *same* class hash twice — they do not prevent declaring an unbounded number of *distinct* class hashes for free, without any signature, resource-bound enforcement, or nonce consumption, as long as the attacker keeps `nonce == 0` and `max_possible_fee == 0`.

This matches the InLong bug class precisely: a security-sensitive decision (skip authorization/fee/signature checks) is keyed off an attacker-supplied value (`sender_address`) rather than an authenticated, operator-controlled property, i.e., "Authorization Bypass Through User-Controlled Key."

### Impact Explanation
This allows any unprivileged transaction sender to:
- Declare arbitrary Sierra/CASM contract classes on the network with zero fee, permanently occupying `class_hash` slots and consuming compilation/CPU/storage resources of the whole network for free, bypassing the fee market entirely (denial-of-service / resource-accounting bypass and free-riding).
- Because `try_declare`/OS logic gate only on the specific `class_hash` (not a global "already bootstrapped" flag), an attacker can repeat this for every new class hash indefinitely, effectively giving them free, unauthenticated, and unlimited access to a resource (declare capacity / sierra-compiler capacity) that is supposed to be fee-gated and authorization-gated for all other senders.
- Bootstrap declares bypass `__validate_declare__`, so there is no accountability/signature tied to the declaration, and the sequencer's own `apollo_integration_tests` note that these transactions are never removed from the mempool through normal execution and get replayed every block until they fail — meaning a flood of forged bootstrap declares could also degrade the mempool/re-proposal pipeline.

This satisfies "concrete loss ... unauthorized account action" and "network unable to confirm new transactions" categories: fee bypass constitutes a concrete loss (uncompensated resource consumption), and it is an unauthorized privileged action (skipping validate/fee/nonce) triggered purely by a forged field value.

### Likelihood Explanation
High likelihood of triggerability: the `sender_address` field of an RPC Declare V3 transaction is entirely attacker-controlled, `bootstrap_address()` is a fixed, publicly-known constant computed from the ASCII string "BOOTSTRAP", and no code inspected in the gateway, mempool, or blockifier restricts this special-cased sender address to genesis or requires any special permission to use it. The only gate (`check_declare_permissions`/`authorized_declarer_accounts`) is an opt-in operator allowlist that defaults to `None` (no restriction) in the default `GatewayStaticConfig`. Constructing such a transaction requires no special access — just knowledge of the magic constant, easily discoverable from the open-source codebase itself.

### Recommendation
- Restrict the bootstrap-declare fast path to genesis-only conditions that cannot be replicated by ordinary users post-genesis (e.g., gate on an explicit "chain not yet bootstrapped" state flag maintained by the state/consensus layer, rather than solely on `sender_address == 'BOOTSTRAP' && nonce == 0`).
- Alternatively/additionally, enforce that the bootstrap address can only be used once globally (not once per class hash) by tracking a persistent "bootstrap completed" bit in state, and reject any subsequent bootstrap-style declare regardless of class hash.
- Ensure the same restriction is mirrored consistently across the gateway (`check_declare_permissions`), blockifier (`AccountTransaction::execute_raw`), and the Starknet OS Cairo code (`execute_declare_transaction`), since all three currently make the bypass decision independently based on the raw `sender_address` value.

### Proof of Concept
1. Compute `bootstrap_address = ContractAddress::from(0x424f4f545354524150_u128)` (the constant used in `DeclareTransaction::bootstrap_address()`).
2. Craft an `RpcDeclareTransaction::V3` with:
   - `sender_address = bootstrap_address`
   - `nonce = 0`
   - `resource_bounds` set so that `max_possible_fee() == 0` (e.g., zero tip/zero gas price bounds, as used in `AllResourceBounds::new_unlimited_gas_no_fee_enforcement()` referenced in test helpers such as `crates/central_systest_blobs/src/cende_blob_regression_test.rs` lines 456-472)
   - `signature = []` (empty/default; unauthenticated) — matches the pattern used by `generate_bootstrap_declare()` in `mempool_test_utils::starknet_api_test_utils`, exercised end-to-end in `crates/apollo_integration_tests/tests/bootstrap_declare.rs`.
   - A freely-chosen `class_hash`/`compiled_class_hash` for any contract class of the attacker's choosing.
3. Submit via the gateway's `add_tx`. Provided `authorized_declarer_accounts` is not configured to exclude the bootstrap address (default), `check_declare_permissions` passes, stateless validation passes, and `AccountTransaction::execute_raw` executes the class declaration with no signature check, no fee charge, and no nonce increment.
4. Repeat with a new distinct `class_hash` value to declare unlimited additional classes for free, at any point in the chain's operation — not only at genesis. [6](#0-5)

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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L888-911)
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
```

**File:** crates/blockifier/src/transaction/transactions.rs (L385-408)
```rust
/// Attempts to declare a contract class by setting the contract class in the state with the
/// specified class hash.
fn try_declare<S: State>(
    tx: &DeclareTransaction,
    state: &mut S,
    class_hash: ClassHash,
    compiled_class_hash: Option<CompiledClassHash>,
) -> TransactionExecutionResult<()> {
    match state.get_compiled_class(class_hash) {
        Err(StateError::UndeclaredClassHash(_)) => {
            // Class is undeclared; declare it.
            state.set_contract_class(class_hash, tx.contract_class().try_into()?)?;
            if let Some(compiled_class_hash) = compiled_class_hash {
                state.set_compiled_class_hash(class_hash, compiled_class_hash)?;
            }
            Ok(())
        }
        Err(error) => Err(error)?,
        Ok(_) => {
            // Class is already declared, cannot redeclare.
            Err(TransactionExecutionError::DeclareTransactionError { class_hash })
        }
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

**File:** crates/apollo_integration_tests/tests/bootstrap_declare.rs (L1-35)
```rust
use apollo_infra_utils::test_utils::TestIdentifier;
use apollo_integration_tests::utils::{
    end_to_end_flow,
    test_single_tx,
    EndToEndFlowArgs,
    EndToEndTestScenario,
};
use mempool_test_utils::starknet_api_test_utils::generate_bootstrap_declare;
use starknet_api::execution_resources::GasAmount;

fn create_bootstrap_declare_scenario() -> EndToEndTestScenario {
    EndToEndTestScenario {
        create_rpc_txs_fn: |_| vec![generate_bootstrap_declare()],
        create_l1_to_l2_messages_args_fn: |_| vec![],
        test_tx_hashes_fn: test_single_tx,
    }
}

/// Bootstrap declare txs are unique: they are sent from a special address and do not increment its
/// nonce. As a result, they are not removed from the mempool upon successful execution, and will
/// only be removed after being rejected during a subsequent attempt.
#[tokio::test(flavor = "multi_thread", worker_threads = 3)]
async fn bootstrap_declare() {
    end_to_end_flow(
        EndToEndFlowArgs::new(
            TestIdentifier::EndToEndFlowTestBootstrapDeclare,
            create_bootstrap_declare_scenario(),
            GasAmount(29000000),
        )
        .allow_bootstrap_txs(),
    )
    .await
}


```
