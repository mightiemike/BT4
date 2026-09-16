### Title
Improper Access Control on Bootstrap Declare allows any unprivileged sender to declare contract classes for free, bypassing signature validation and fees - ([File: crates/blockifier/src/transaction/account_transaction.rs])

### Summary
A special "bootstrap declare" code path, intended only for the one-time genesis bootstrapping of a Starknet system, has no gating besides transaction field values that any external, unprivileged sender fully controls. Any account (real or non-existent) can submit a `DECLARE` transaction with `sender_address = bootstrap_address()` ('BOOTSTRAP'), `nonce = 0`, and zero resource bounds, and have the blockifier skip `__validate_declare__` execution, skip nonce incrementing, and skip fee charging entirely, permanently declaring an arbitrary class into global state at no cost and without any signature check.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` in [1](#0-0)  only checks three self-supplied transaction fields — `sender_address == bootstrap_address()`, `nonce == Nonce(0)`, and `!charge_fee` — with no additional binding to actual genesis conditions (e.g., current block number, chain state, or an authenticated bootstrapper).

This flag is consumed in `AccountTransaction::execute_raw` in [2](#0-1) , where if `is_bootstrap_declare` returns true, the transaction:
- Skips `perform_pre_validation_stage` (nonce/fee checks against real account state),
- Skips running `__validate_declare__` (no signature verification is performed at all),
- Skips fee charging,
- Directly runs `run_execute`, which (per the analogous Starknet OS Cairo logic in [3](#0-2) ) writes the declared `class_hash -> compiled_class_hash` mapping directly into `contract_class_changes`, gated only by `prev_value == 0` (i.e., the class hasn't been declared before).

Because `'BOOTSTRAP'` is a fixed, publicly known constant address (`0x424f4f545354524150`, see `bootstrap_address()`), and because the nonce for this pseudo-account is never incremented (the code path returns before `check_and_increment_nonce`/`perform_pre_validation_stage` runs), the "bootstrap" nonce-0 precondition remains permanently satisfiable — it is not restricted to genesis. There is no on-chain notion of "the system has already been bootstrapped" that disables this path afterward; the only restriction is per-class-hash (`prev_value == 0` in the dict update), so a distinct class hash can always be freely declared this way by anyone at any time.

The gateway's `authorized_declarer_accounts` allow-list (`check_declare_permissions` in [4](#0-3) ) is an optional, disabled-by-default operator config and does not inherently special-case or block the bootstrap address; if unset (the default), it imposes no restriction on who may use `sender_address = 'BOOTSTRAP'`.

### Impact Explanation
This is a direct improper-access-control analog to the reported novajoin CVE: a code path meant to be usable only by a privileged party in a specific one-time context (genesis bootstrap) is reachable by any unprivileged transaction sender with no authentication, because the only "authorization" check is a self-controlled transaction field. Consequences:
- Any external sender can permanently write arbitrary attacker-chosen contract classes into the committed global state without paying fees and without any signature/validation check — a form of unauthorized state mutation performed outside the intended account-abstraction/fee model.
- Because execution is fee-free and validation-free, this could be repeated for many distinct class hashes indefinitely, at zero cost to the attacker, undermining the deliberate design invariant that all state-changing declare actions be authenticated and paid for.

### Likelihood Explanation
High reachability: the only requirements are knowledge of the constant `bootstrap_address()` (visible in this same open-source codebase) and crafting a V3 declare transaction with `nonce = 0` and zero resource bounds — both fully controlled by the attacker with no special privileges, keys, or node access needed.

### Recommendation
Restrict the bootstrap-declare bypass so it can only be exercised during genuine genesis bootstrapping (e.g., gate it on block number == 0 / a one-time system flag that is cleared after first use, and/or require it to be injected only by the sequencer itself rather than accepted from externally submitted RPC transactions), rather than relying solely on attacker-suppliable transaction fields (`sender_address`, `nonce`, `charge_fee`) as the sole access-control mechanism.

### Proof of Concept
1. Craft a `DECLARE` (V3) RPC transaction with:
   - `sender_address = 0x424f4f545354524150` ('BOOTSTRAP', per `DeclareTransaction::bootstrap_address()`),
   - `nonce = 0`,
   - `resource_bounds` set so `max_possible_fee == 0` (e.g., via `ValidResourceBounds::create_for_testing_no_fee_enforcement()` style zero bounds, as used in `generate_bootstrap_declare()` in [5](#0-4) ),
   - Any signature (unchecked, since `__validate_declare__` is never invoked),
   - A previously-undeclared arbitrary `class_hash`/`compiled_class_hash`.
2. Submit via the gateway `add_tx` (no `authorized_declarer_accounts` allow-list configured, the default).
3. The transaction bypasses `perform_pre_validation_stage` and `__validate_declare__`, and `execute_raw` (per [6](#0-5) ) directly declares the class with `TransactionExecutionInfo::default()` (no fee charged), as confirmed by the existing test `test_bootstrap_declare` in [7](#0-6) , which demonstrates the class gets declared with "no fees, nonce bump, etc."

Note: I was unable to fully verify, within the available tooling, whether any additional height/genesis-only gate exists elsewhere in the batcher/consensus proposal-building path that might restrict this transaction type to block 0 only; the `bootstrap_declare` integration test (`crates/apollo_integration_tests/tests/bootstrap_declare.rs`) uses `.allow_bootstrap_txs()` on the test harness, suggesting bootstrap declares may require an explicit opt-in flag in the full end-to-end flow — this flag's enforcement point (and whether it is a real network-wide consensus rule vs. a test-only harness setting) could not be located and confirmed with the tools available. A Devin session with full repository access is recommended to trace `allow_bootstrap_txs` and confirm whether production sequencers reject bootstrap-address declares after genesis at any other layer.

### Citations

**File:** crates/starknet_api/src/executable_transaction.rs (L246-255)
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

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L905-991)
```rust
#[rstest]
#[case::valid(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    class_hash: class_hash!(7_u64),
    compiled_class_hash: CompiledClassHash(8_u64.into()),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "DeclareTransactionCasmHashMissMatch")]
#[case::poseidon_declare_tx(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    class_hash: class_hash!(7_u64),
    compiled_class_hash: CompiledClassHash(8_u64.into()),
    ..Default::default()
}), HashVersion::V1)]
#[should_panic(expected = "UninitializedStorageAddress")]
#[case::wrong_tx_version(DeclareTransaction::V2(DeclareTransactionV2 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "InvalidNonce")]
#[case::wrong_nonce(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    nonce: Nonce(felt!(1_u64)),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "UninitializedStorageAddress")]
#[case::wrong_sender_address(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ContractAddress(PatriciaKey::from(1_u128)),
    ..Default::default()
}), HashVersion::V2)]
#[should_panic(expected = "InsufficientResourceBounds")]
#[case::non_trivial_resource_bounds(DeclareTransaction::V3(DeclareTransactionV3 {
    sender_address: ApiExecutableDeclareTransaction::bootstrap_address(),
    resource_bounds: ValidResourceBounds::AllResources(AllResourceBounds {
        l1_gas: ResourceBounds::default(),
        l2_gas: ResourceBounds{max_amount: GasAmount(1), max_price_per_unit: GasPrice(1)},
        l1_data_gas: ResourceBounds::default(),
    }),
    ..Default::default()
}), HashVersion::V2)]
fn test_bootstrap_declare(
    block_context: BlockContext,
    #[case] declare_tx: DeclareTransaction,
    #[case] hash_version: HashVersion,
) {
    let class_info = calculate_class_info_for_testing(
        FeatureContract::Empty(CairoVersion::Cairo1(RunnableCairo1::Casm)).get_class(),
    );
    let contract_class = class_info.contract_class();
    let mut executable_declare = ApiExecutableDeclareTransaction {
        tx: declare_tx.clone(),
        tx_hash: TransactionHash::default(),
        class_info,
    };

    // Update compiled_class_hash in V3 declare txs to match the contract class with the given hash
    // version.
    if let DeclareTransaction::V3(tx) = &mut executable_declare.tx {
        if let ContractClass::V1((casm, _)) = &contract_class {
            tx.compiled_class_hash = casm.hash(&hash_version);
        }
    }
    let compiled_class_hash = executable_declare.tx.compiled_class_hash();
    let declare_account_tx = AccountTransaction::new_for_sequencing(
        ApiExecutableTransaction::Declare(executable_declare),
    );

    let mut state = CachedState::from(DictStateReader::default());
    let res = declare_account_tx.execute(&mut state, &block_context).unwrap();

    // Check declaration.
    assert_eq!(
        state.get_compiled_class_hash(declare_tx.class_hash()).unwrap(),
        compiled_class_hash
    );

    // Ensure the only change is the class declaration: no fees, nonce bump, etc.
    assert_eq!(res, TransactionExecutionInfo::default());
    assert_eq!(
        state.to_state_diff().unwrap().state_maps,
        StateMaps {
            compiled_class_hashes: HashMap::from([(declare_tx.class_hash(), compiled_class_hash)]),
            declared_contracts: HashMap::from([(declare_tx.class_hash(), true)]),
            ..Default::default()
        }
    );
}
```
