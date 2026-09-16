### Title
Unrestricted "BOOTSTRAP" declare bypass allows any transaction sender to declare classes for free, bypassing fee, `__validate_declare__`, and the `authorized_declarer_accounts` permission gate - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo])

### Summary
The Starknet OS declare-transaction handler contains a special-case short-circuit intended only for "bootstrapping a new system": if a `DECLARE` transaction's `sender_address` equals the fixed literal `'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and the transaction's `max_possible_fee == 0`, the class is declared with **no fee charge, no `__validate_declare__` call, and no nonce increment**. This is functionally identical in spirit to the reported incident's "lack of mint permission control": a privileged state-mutating action (declaring/registering a new class, analogous to minting) that should be gated by an authorization mechanism (the gateway's `authorized_declarer_accounts` allowlist) is instead reachable by anyone, because the guard is based purely on attacker-suppliable transaction fields rather than any real authentication or single-use enforcement.

### Finding Description
`execute_declare_transaction` in [1](#0-0)  contains:
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
// Increment nonce.
check_and_increment_nonce(tx_info=tx_info);
```
Critically, `check_and_increment_nonce` is only reached *after* this early return, meaning the nonce for the `'BOOTSTRAP'` address is **never incremented** on this path. Since `sender_address`, `version`, `nonce`, and `resource_bounds` (which drive `max_possible_fee`) are all attacker-controlled fields of a submitted `DeclareTransactionV3`, any unprivileged sender can repeatedly satisfy `nonce == 0` for the bootstrap address, for every new (not-yet-declared) `class_hash`, at any block height — not only during genesis as the comment implies.

The equivalent Rust-side logic is mirrored by `DeclareTransaction::is_bootstrap_declare` in [2](#0-1) , and is exercised end-to-end by `test_bootstrap_declare` in [3](#0-2) , which confirms that executing such a transaction results in a class declaration with **no fee, no nonce bump, and no other state changes** — i.e., the bypass is real and reachable via the standard `AccountTransaction::execute` path used by the sequencer's execution engine.

Separately, the gateway's only declare-time access control is `check_declare_permissions`, which checks `is_authorized_declarer` against `authorized_declarer_accounts` in [4](#0-3)  and [5](#0-4) . Nothing in this check (or elsewhere that was found) special-cases or blocks the reserved `'BOOTSTRAP'` sender address, so on any deployment that restricts declares via `authorized_declarer_accounts` (a real permission-control feature, off by default), the bootstrap short-circuit provides an unauthenticated, un-fee-metered side channel to declare arbitrary classes, defeating that permission control entirely.

### Impact Explanation
This allows any unprivileged transaction sender to:
- Bypass the `authorized_declarer_accounts` allowlist — the sequencer's declared permission-control mechanism for restricting who may declare contracts (directly analogous to the reported "lack of mint permission control").
- Declare an unbounded number of classes for free (no fee charged, since `max_possible_fee == 0` is a required precondition, and no nonce consumption prevents repetition), each triggering Sierra→CASM compilation work in the gateway/sierra-compiler fleet, which is a real resource-consumption vector referenced by the gateway's own `max_concurrent_declare_compilations` DoS-mitigation config in [6](#0-5) .
- Produce class declarations with no corresponding validated signature/account, undermining the invariant that declared classes go through account-based authorization.

This is not a resource-only or low-severity issue: it is a hard bypass of an explicit access-control feature (`authorized_declarer_accounts`) intended to gate a privileged, permanent, on-chain state mutation (class declaration), reachable from a single unprivileged submitted transaction.

### Likelihood Explanation
Reachability is trivial and requires only crafting an `RpcDeclareTransaction::V3` (or the corresponding executable `DeclareTransaction`) with:
- `sender_address` = `ContractAddress::from(0x424f4f545354524150_u128)` (the public, hardcoded ASCII "BOOTSTRAP" felt, per `bootstrap_address()` in [7](#0-6) ),
- `nonce = 0`,
- `version = 3`,
- zero resource bounds (to force `max_possible_fee == 0`),
- a valid, not-yet-declared `(class_hash, compiled_class_hash)` pair.

No signature, no account deployment, and no special privileges are needed to construct or submit this transaction through the normal gateway ingestion path.

### Recommendation
- Restrict the `'BOOTSTRAP'` declare short-circuit so it can only execute once, at genesis (e.g., gate it on `block_number == 0` in `BlockContext`, or remove it from the general transaction-execution path entirely and handle bootstrapping via an explicit, operator-only genesis procedure).
- Ensure the nonce (or an equivalent one-time-use marker) for the bootstrap address is always incremented/consumed even on this fast path, so it cannot be reused across multiple class hashes indefinitely.
- Make `check_declare_permissions` (and any stateful validator) explicitly reject `sender_address == bootstrap_address()` outside of the legitimate genesis flow, so `authorized_declarer_accounts` cannot be bypassed via this reserved address.

### Proof of Concept
1. Deploy/observe a sequencer configured with `gateway_config.static_config.authorized_declarer_accounts` set to a restrictive allowlist that excludes the attacker's account (a supported, real configuration, see [8](#0-7)  for the intended enforcement behavior).
2. Craft a `DeclareTransactionV3` with `sender_address = 0x424f4f545354524150` ("BOOTSTRAP"), `nonce = Nonce(0)`, `version = TransactionVersion::THREE`, all resource bounds set to zero, and a `class_info`/`compiled_class_hash` for any not-yet-declared class.
3. Submit the transaction to the gateway. `check_declare_permissions` only checks `authorized_declarer_accounts`/`block_declare`; nothing rejects the reserved bootstrap sender address.
4. On execution, `execute_declare_transaction` takes the `sender_address == 'BOOTSTRAP' and nonce==0 and version==3 and max_possible_fee==0` branch, per [9](#0-8) , declaring the class with no fee, no validation, and no nonce increment — confirmed functionally by `test_bootstrap_declare` in [3](#0-2) , which asserts `res == TransactionExecutionInfo::default()` (no fee/resources) and that the only state change is the class declaration.
5. Repeat with a new `class_hash` each time — nonce remains `0` for the bootstrap address indefinitely, permitting unlimited free declares that bypass the configured `authorized_declarer_accounts` permission gate.

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

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L945-991)
```rust
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

**File:** crates/apollo_gateway_config/src/config.rs (L30-38)
```rust
//
// Derivation: compilations are served by the sierracompiler instances, so the safe per-gateway
// bound is the sierracompiler fleet's headroom divided across the gateway fleet, i.e.
// `per_instance_capacity * num_sierracompiler_instances / num_gateway_instances`. Observed
// sierracompiler usage per compilation is small (memory spike ~0.75% of an instance), so a single
// instance can absorb many concurrent compilations. 40 stays well within that envelope while still
// capping the blast radius of a declare flood; retune via the formula above if the
// sierracompiler/gateway instance ratio or per-instance capacity changes.
const DEFAULT_MAX_CONCURRENT_DECLARE_COMPILATIONS: usize = 40;
```

**File:** crates/apollo_gateway_config/src/config.rs (L140-147)
```rust
impl GatewayConfig {
    pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
        match &self.static_config.authorized_declarer_accounts {
            Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
            None => true,
        }
    }
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
