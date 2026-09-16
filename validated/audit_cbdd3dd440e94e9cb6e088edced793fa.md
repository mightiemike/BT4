### Title
Unauthenticated Bypass of Declare-Transaction Validation and Fees via the Reserved `BOOTSTRAP` Sender Address - (File: `crates/starknet_api/src/executable_transaction.rs`)

### Summary
Any unprivileged transaction sender can submit an ordinary `RpcTransaction::Declare` (V3) through the gateway with `sender_address` set to the reserved bootstrap constant, `nonce = 0`, and zero-fee resource bounds. This satisfies `DeclareTransaction::is_bootstrap_declare()` and causes both the Rust blockifier and the Starknet OS Cairo program to skip `__validate_declare__`, skip nonce incrementing, and skip fee charging entirely — writing the class hash directly into state. The bootstrap short-circuit is intended only for genesis system bootstrapping, but nothing in the gateway or blockifier restricts it to genesis time or to a privileged caller.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` treats any V3 declare transaction as a "bootstrap declare" purely based on transaction *content*, not on any privileged execution context: [1](#0-0) 

`AccountTransaction::execute_raw` checks this predicate and, when true, skips `perform_pre_validation_stage` (nonce/fee checks), skips `__validate_declare__`, and skips `handle_fee`, returning a default `TransactionExecutionInfo` after only running the class declaration itself: [2](#0-1) 

The same bypass is mirrored in the Starknet OS Cairo program, so the behavior is consistently committed to the proven state (all honest nodes agree, but the state itself is polluted): [3](#0-2) 

The only gateway-side gate on declare transactions is `check_declare_permissions`, which merely checks an optional allowlist (`authorized_declarer_accounts`); when this config is unset (the common/default posture for permissionless declare), any sender address — including the bootstrap constant — passes unimpeded: [4](#0-3) [5](#0-4) 

There is no additional check anywhere in the gateway/mempool/blockifier path verifying that a declare transaction using the bootstrap sender address originates only from a genesis/bootstrap flow, is only accepted once at chain start, or is otherwise restricted to a privileged component. The existing test/tooling code (`generate_bootstrap_declare`, `bootstrap_declare.rs` integration test) confirms the mechanism is reachable purely by constructing an ordinary RPC transaction with this sender address and zero-fee resource bounds: [6](#0-5) [7](#0-6) 

This is directly analogous to the reported bug class: a state-mutating request (declare/"publish") succeeds without the validation (account ownership/`__validate_declare__`) and authorization (fee payment) checks that are supposed to gate it, because a special-cased "trusted" code path is reachable by an untrusted caller.

### Impact Explanation
- **Unauthorized account action**: declaration normally requires the sender's account contract to authorize the action via `__validate_declare__`. This path lets anyone declare a class hash under the `BOOTSTRAP` address without any such authorization, and without paying the declare fee.
- **Permanent freezing / griefing of legitimate bootstrap flow**: `dict_update` in both blockifier and the OS enforces `prev_value == 0`, i.e., each class hash may be declared only once via this bootstrap mechanism. An attacker who front-runs a real genesis bootstrap declaration for a specific `class_hash` (or simply spams the bootstrap path with declares) can permanently prevent the legitimate bootstrap declare of that class from succeeding, since the class is already marked declared.
- **Fee-bypass / free declares**: because `charge_fee` becomes false whenever resource bounds are set to zero, and `is_bootstrap_declare` only requires `!charge_fee` plus the fixed sender/nonce, this also functions as a free, unauthenticated declare primitive that bypasses the network's declare fee economics, independent of the `authorized_declarer_accounts` allowlist unless that allowlist specifically excludes the bootstrap address.

### Likelihood Explanation
Reachable by any single external caller with no special privileges — only requires crafting a `Declare` V3 RPC transaction with `sender_address = DeclareTransaction::bootstrap_address()`, `nonce = 0`, and resource bounds that yield `charge_fee = false` (e.g., `ValidResourceBounds::create_for_testing_no_fee_enforcement`-equivalent zero bounds). No compromised keys, no operator/proposer privilege, and no network-level attack is needed — it is a standard `add_transaction` gateway submission.

### Recommendation
Restrict the bootstrap-declare short-circuit so it cannot be triggered by ordinary externally-submitted transactions post-genesis:
- Gate `is_bootstrap_declare`/its execution path behind an explicit sequencer/genesis-only execution flag (not transaction content alone), so it can only be exercised by the component that performs genesis bootstrapping.
- If bootstrap declares must remain reachable via the public gateway, reject any declare transaction using the bootstrap sender address once the chain has left genesis (e.g., track a "bootstrap completed" flag in state or config), and/or unconditionally require `authorized_declarer_accounts` to explicitly exclude the bootstrap address by default.
- Ensure the Cairo OS program enforces the same genesis-only restriction rather than relying solely on transaction fields.

### Proof of Concept
1. Construct an `RpcDeclareTransaction::V3` with:
   - `sender_address = DeclareTransaction::bootstrap_address()` (`0x424f4f545354524150`)
   - `nonce = Nonce(Felt::ZERO)`
   - `resource_bounds` set so all resource max amounts are 0 (yields `charge_fee = false`, as used in `generate_bootstrap_declare`)
   - Any valid `class_hash`/`compiled_class_hash`/contract class pair.
2. Submit via the gateway's `add_tx` (no authentication beyond default config, assuming `authorized_declarer_accounts` is unset).
3. `check_declare_permissions` passes (no allowlist configured); `AccountTransaction::execute_raw` detects `is_bootstrap_declare() == true` and short-circuits to declare the class hash directly, without running `__validate_declare__` and without charging any fee.
4. The class hash is now permanently marked declared in state; a subsequent legitimate bootstrap declare for the same class hash will fail due to the `prev_value == 0` invariant in the `dict_update` calls.

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

**File:** crates/apollo_gateway/src/gateway.rs (L228-236)
```rust
        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }

        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;
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

**File:** crates/apollo_integration_tests/tests/bootstrap_declare.rs (L19-33)
```rust
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
