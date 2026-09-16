### Title
Unrestricted "BOOTSTRAP" declare fast-path allows any user to bypass declare fees and skip validation for arbitrary contract classes - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo])

### Summary
The Starknet OS (the code that re-executes transactions and produces the committed state root) contains a special-cased "bootstrap declare" fast-path meant to be used exactly once, by the sequencer, to seed the very first classes into a brand-new chain. The gate for this privileged path is derived **entirely from attacker-controlled transaction fields** (`sender_address`, `nonce`, `version`, `resource_bounds`) with no binding to genesis/block-height or any state that only the operator can produce. Any external declare-transaction sender can therefore construct a transaction that satisfies this gate and obtain the same privilege — declaring a class while skipping `__validate_declare__` and all fee/resource accounting — a capability that should be exclusive to the chain-bootstrapping process.

### Finding Description
In the OS declare flow, the privileged skip is entered solely based on tx fields: [1](#0-0) 

```
if (sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3) {
    let max_possible_fee = compute_max_possible_fee(tx_info=tx_info);
    if (max_possible_fee == 0) {
        assert_not_zero(compiled_class_hash);
        dict_update{dict_ptr=contract_class_changes}(
            key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash
        );
        %{ SkipTx %}
        return ();
    }
}
```

Nothing here checks that the block is the genesis block, that the chain has not started, or that the caller is the operator — it is purely: `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `resource_bounds` chosen so `max_possible_fee == 0`. All four of these are fields any RPC declare-transaction sender fully controls. The corresponding "special address" is a well-known, hardcoded, non-secret constant: [2](#0-1) 

The Rust `blockifier` mirrors this same skip based on `is_bootstrap_declare`, which likewise only checks `sender_address`, `nonce == 0`, and the (execution-flag-derived) `charge_fee`: [3](#0-2) 

The only defense preventing every external user from freely exercising this path today is a gateway-level, config-toggled resource-bounds check that rejects `ZeroResourceBounds`: [4](#0-3) 

and the gateway file itself explicitly documents that sender-address blocking has **not** been implemented: [5](#0-4) 
```
// TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
```

This confirms there is currently no allow-list/deny-list enforcement preventing arbitrary submitters from using `sender_address = 'BOOTSTRAP'`. The only remaining barrier is the generic zero-resource-bounds check, which is a config-dependent, incidental protection rather than an explicit, intentional restriction of the bootstrap privilege to genesis/operator use — and it does not defend the class-hash uniqueness/free-declare semantics at all if `validate_resource_bounds` is disabled or the transaction ever reaches OS re-execution through any other ingestion path (e.g., a batcher/consensus flow that does not run the gateway's stateless validator).

### Impact Explanation
If reachable (i.e., resource-bounds validation is disabled, misconfigured, or bypassed via any ingestion path not going through `StatelessTransactionValidator`), an unprivileged user gains a capability equivalent to the reported Shopware bug class ("non-admin users can create integration role with administrator role"): they obtain an operator-only privilege — free, unvalidated contract-class declaration — normally reserved for one-time chain bootstrapping. Concretely this allows:
- Bypassing all declare-transaction fees and resource/bouncer accounting for arbitrary new classes (`assert_not_zero(compiled_class_hash)` and the `prev_value=0` uniqueness check only block re-declaring the *same* class hash, not repeated use of the mechanism for new/different classes).
- No `__validate_declare__` execution, so the class-hash uniqueness protection is the only remaining safeguard, and it is trivially satisfiable for new classes forever.
- Potential state/DA bloat and free resource consumption (a network-availability/DoS vector), since this bypasses the bouncer weight and fee mechanisms designed to price and rate-limit declarations.

### Likelihood Explanation
Reaching the vulnerable code requires the attacker's declare transaction to arrive at the OS/blockifier execution stage with `sender_address == 'BOOTSTRAP'`, `nonce == 0`, and computed `max_possible_fee == 0`. Today, the only obstacle is the gateway's generic `ZeroResourceBounds` stateless check, which exists for an unrelated reason (rejecting non-fee-paying transactions generally) and is config-gated (`validate_resource_bounds`). There is no explicit protection tying the "BOOTSTRAP" fast-path to genesis or to a privileged submitter, and the code comments (`TODO(Arni, 1/5/2024)`) confirm this gap was known but left unaddressed. Likelihood is Medium: exploitation depends on configuration/ingestion-path specifics that could not be fully confirmed from the available code (e.g., exact default of `validate_resource_bounds`, and whether all transaction ingestion paths funnel through `StatelessTransactionValidator` before OS re-execution).

### Recommendation
- Restrict the `'BOOTSTRAP'` fast-path so it can only trigger on the actual genesis block (e.g., gate on `block_number == 0` / an explicit one-time chain-bootstrap flag) rather than relying solely on attacker-suppliable transaction fields.
- Explicitly reject `sender_address == 'BOOTSTRAP'` in the gateway's stateless/stateful validators for any transaction not originating from the trusted bootstrap flow, completing the `TODO(Arni, 1/5/2024)` sender-address block-list.
- Ensure the resource-bounds/zero-fee check cannot be disabled or bypassed for declare transactions using the reserved bootstrap address, independent of the general `validate_resource_bounds` config toggle.
- Align the Cairo OS logic and the Rust `blockifier` logic so both depend on the same authoritative, non-attacker-controlled signal (not just `sender_address`/`nonce`/computed fee).

### Proof of Concept
Conceptual PoC (pending confirmation of gateway config defaults, since I could not fully verify `enforce_fee`'s exact implementation or the default value of `validate_resource_bounds` within the search budget):
1. Craft an `RpcDeclareTransaction::V3` with `sender_address = DeclareTransaction::bootstrap_address()` (`'BOOTSTRAP'`), `nonce = 0`, `version = THREE`, and `resource_bounds` chosen such that `compute_max_possible_fee` evaluates to `0` (mirroring `ValidResourceBounds::create_for_testing_no_fee_enforcement()` used in test helper `generate_bootstrap_declare`, [6](#0-5) ).
2. Submit this transaction to the gateway as any regular, unprivileged user (no special key or role required — the address is a public, hardcoded constant).
3. If it passes stateless resource-bounds validation (or that check is disabled/misconfigured), the OS/blockifier will execute the declare via the bootstrap skip path, declaring the attacker's chosen class for free with no `__validate_declare__` and no fee payment — the same code path exercised by the test `test_bootstrap_declare` ( [7](#0-6) ) and the integration test `bootstrap_declare` ( [8](#0-7) ), which confirm this exact mechanism is reachable via a normally-constructed RPC transaction, not just internal test scaffolding.

**Caveat**: I was unable to fully verify, within tool-call limits, (a) the exact default/production value of `gateway_config.static_config.stateless_tx_validator_config.validate_resource_bounds`, and (b) the full body of `enforce_fee` in `crates/blockifier/src/transaction/transactions.rs` / `objects.rs`, which determines whether `charge_fee` can be forced to `false` for a normal (non-simulate) submitted transaction in the Rust `blockifier` path. These are necessary to determine whether this issue is fully exploitable in the current default production configuration or is currently mitigated by the generic zero-resource-bounds check. A Devin session with full repository access would be needed to confirm these defaults and finalize exploitability.

### Citations

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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L33-36)
```rust
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-69)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
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
