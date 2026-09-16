### Title
Unrestricted, replayable `BOOTSTRAP`-sender exemption lets any unprivileged declarer skip signature validation and fee enforcement - ([File: crates/starknet_api/src/executable_transaction.rs])

### Summary
The sequencer contains a special-case exemption, intended only for one-time chain-genesis bootstrapping, that lets a Declare transaction sent from a fixed, publicly-known address (`'BOOTSTRAP'`) skip `__validate_declare__`, fee charging, and nonce incrementation. Unlike the wolfSSL bug (where a trust exemption meant for one narrow category of certificate was mistakenly applied to an untrusted category), here the exemption is defined purely by superficial, fully attacker-controlled transaction fields (sender address constant, nonce = 0, version = 3, zero resource bounds) with no binding to genesis/block-height or any operator-only authorization. Because the nonce for this sender is never incremented, the condition is repeatable indefinitely by any transaction sender, not a one-time genesis action.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` and `bootstrap_address()` define the exemption purely in terms of the transaction's own fields: [1](#0-0) 

The Starknet OS enforces the same unconditional check at the Cairo level, and on match it declares the class hash and returns immediately, bypassing `__validate_declare__`, fee charge, and nonce increment: [2](#0-1) 

The only "single use" property enforced is per-class-hash (`dict_update{...}(key=[class_hash_ptr], prev_value=0, new_value=compiled_class_hash)`), which only prevents redeclaring the *same* class hash twice - it does not prevent the `BOOTSTRAP` mechanism itself from being invoked repeatedly with new class hashes. The accompanying integration test explicitly documents that the nonce is never bumped for this sender, so the same tx (or new ones with the same nonce=0) can be resubmitted indefinitely: [3](#0-2) 

The blockifier's execution-level test confirms the effect: a `Declare` tx from the bootstrap address with zero resource bounds executes successfully with **no state change other than the class declaration** - no fee is charged and no nonce is bumped: [4](#0-3) 

None of `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and `max_possible_fee == 0` require possession of any private key, deployed account, or special network privilege - they are simply RPC-transaction field values that any sender can set on an ordinary `DECLARE` transaction submitted through the gateway. There is no check gating this path to genesis block, chain bootstrap phase, or an authorized operator identity, in contrast to the (separately-existing, config-driven) `authorized_declarer_accounts` allowlist mechanism used for ordinary declares: [5](#0-4) 

I was not able to confirm within the available searches whether `apollo_gateway`/`apollo_mempool` production code applies any additional gate specific to the `BOOTSTRAP` sender address before admitting such a transaction into the mempool (the only related flag found, `allow_bootstrap_txs()`, appears solely in test-harness code under `apollo_integration_tests`, not in the production gateway/mempool crates). This is a material gap in my verification and should be checked explicitly.

### Impact Explanation
If the gateway/mempool does not independently reject Declare transactions using the `BOOTSTRAP` sender address outside of an explicit genesis phase, any unprivileged network participant can:
- Declare arbitrary Sierra/CASM classes for free, without any `__validate_declare__` signature check and without paying fees, an unauthorized action that bypasses both the fee market and account-based authorization model.
- Repeat this indefinitely (since nonce is never incremented for this sender), enabling free, signature-less resource consumption for class declaration - a fee/authorization-bypass with permanent, systemic effect on network economics and declarer authorization guarantees.

### Likelihood Explanation
All conditions required to enter the bypass branch (`sender_address`, `nonce`, `version`, `resource_bounds`) are ordinary, attacker-controlled fields of a standard V3 Declare transaction; no cryptographic material or special privilege is needed to construct such a transaction and submit it via the gateway's ordinary transaction-submission RPC.

### Recommendation
Scope the `BOOTSTRAP` exemption to a genuinely one-time, operator/genesis-only action - e.g., require it only when the chain is at block 0 / has not yet processed any transaction, and/or gate it behind an explicit sequencer configuration flag (similar to `authorized_declarer_accounts`) rather than relying solely on transaction-supplied field values (`sender_address == 'BOOTSTRAP'`, `nonce == 0`, `resource_bounds == 0`). Additionally, consider marking the bootstrap address as permanently "used" after the first successful bootstrap declare so the exemption cannot be invoked more than once.

### Proof of Concept
1. Construct an ordinary `DECLARE` (V3) RPC transaction with:
   - `sender_address = 0x424f4f545354524150` (`'BOOTSTRAP'`),
   - `nonce = 0`,
   - `resource_bounds` = all zero (as used by `ValidResourceBounds::create_for_testing_no_fee_enforcement()` in `crates/mempool_test_utils/src/starknet_api_test_utils.rs:585-595`),
   - an arbitrary class + matching `compiled_class_hash`,
   - an empty/default signature (no `__validate_declare__` is ever invoked for this path).
2. Submit it to the gateway like any normal declare transaction.
3. Per `is_bootstrap_declare`/the OS `transaction_impls.cairo` bootstrap branch, the class is declared with no fee charge, no nonce bump, and no validation. Since the nonce remains 0, repeat with new class hashes indefinitely.

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

**File:** crates/apollo_integration_tests/tests/bootstrap_declare.rs (L19-22)
```rust
/// Bootstrap declare txs are unique: they are sent from a special address and do not increment its
/// nonce. As a result, they are not removed from the mempool upon successful execution, and will
/// only be removed after being rejected during a subsequent attempt.
#[tokio::test(flavor = "multi_thread", worker_threads = 3)]
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

**File:** crates/apollo_gateway/src/gateway_test.rs (L798-820)
```rust
#[rstest]
#[tokio::test]
async fn test_unauthorized_declare_config(mut mock_dependencies: MockDependencies) {
    let authorized_address = contract_address!("0x1");
    mock_dependencies.config.static_config.authorized_declarer_accounts =
        Some(vec![authorized_address]);

    let gateway = mock_dependencies.gateway();
    let rpc_declare_tx = declare_tx();

    // Ensure the sender address is different from the authorized address.
    assert_ne!(
        rpc_declare_tx.calculate_sender_address().unwrap(),
        authorized_address,
        "Sender address should not be authorized"
    );

    let gateway_output_code_error = gateway.add_tx(rpc_declare_tx, None).await.unwrap_err().code;
    let expected_code_error =
        StarknetErrorCode::KnownErrorCode(KnownStarknetErrorCode::UnauthorizedDeclare);

    assert_eq!(gateway_output_code_error, expected_code_error);
}
```
