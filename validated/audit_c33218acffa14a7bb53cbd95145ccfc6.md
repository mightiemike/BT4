## Title
Permissionless "bootstrap declare" bypass allows any user to bypass declare fees, authorization, and validation — (File: `crates/starknet_api/src/executable_transaction.rs`)

### Summary
The sequencer contains a special-cased "bootstrap declare" path intended only for one-time genesis initialization of a new Starknet system, allowing a `Declare` transaction sent from a fixed, publicly-known sender address (`'BOOTSTRAP'`) with `nonce == 0`, `version == 3`, and zero resource bounds to skip `__validate_declare__`, fee charging, and nonce incrementing entirely. Because the "bootstrap" address is a hardcoded constant rather than a privileged/authenticated identity, and because I could not find any gating (e.g., a block-height/genesis-only check) restricting use of this path to system initialization, this closely mirrors the phpVMS advisory's bug class: a legacy/special-purpose feature that remains reachable by any unauthenticated/unprivileged caller because the code checks a fixed identifier rather than real authorization. [1](#0-0) 

### Finding Description
`DeclareTransaction::is_bootstrap_declare` and `DeclareTransaction::bootstrap_address` define a special sender identity — a hardcoded felt representation of the string `'BOOTSTRAP'` — that is not a real deployed account and requires no signature verification, no `__validate_declare__` call, and no fee payment: [1](#0-0) 

The Starknet OS Cairo implementation of `execute_declare_transaction` implements the actual bypass: if `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`, and the computed max possible fee is `0`, the class hash is declared directly via `dict_update` and the transaction is skipped (`%{ SkipTx %}`) without running `__validate_declare__`, incrementing any nonce, or charging any fee: [2](#0-1) 

This same bypass is mirrored in the Rust blockifier execution path (`try_declare` / `DeclareTransaction::run_execute`), and is exercised by tests (`test_bootstrap_declare`, `generate_bootstrap_declare`, `bootstrap_declare.rs`) confirming that a `Declare` V3 transaction sent from `bootstrap_address()` with `nonce = 0` and no fee enforcement produces `TransactionExecutionInfo::default()` — i.e., no validation call, no fee transfer, no nonce bump — while still declaring the class: [3](#0-2) [4](#0-3) 

At the gateway layer, `check_declare_permissions` only gates declares by an `is_authorized_declarer` allow/deny-list keyed on `sender_address`, and a global `block_declare` flag — there is no visible restriction preventing an already-running (post-genesis) chain from accepting a `Declare` transaction whose `sender_address` happens to equal the hardcoded bootstrap constant: [5](#0-4) 

Because the "bootstrap" identity is derived purely from a known constant address rather than any node-operator secret, block height, or one-time-use enforcement I could locate, any unprivileged party who can construct and submit a well-formed `Declare` V3 transaction is a candidate to exploit this path — provided a declarer allow-list is not restricting the constant address and the node's declare permissions are otherwise permissive (the default/common configuration for public declare on many Starknet deployments).

### Impact Explanation
If reachable post-genesis by an arbitrary declarer:
- **Fee/resource-accounting bypass**: an attacker can declare new classes for free (no signature, no `__validate_declare__` execution, no fee charged), undermining the fee market's guarantee that declare transactions are paid for, which is a resource-accounting invariant of the sequencer's fee/bouncer subsystem.
- **Authorization bypass**: the declare goes through without running any account-contract validation logic, effectively acting as an unauthenticated declare channel — analogous to the phpVMS "unauthenticated access to an internal process that mutates state" bug class (CWE-284/306/862): missing authorization on a legacy/special bootstrap capability.
- **State-divergence risk**: `dict_update` with `prev_value=0` in the OS guards against re-declaring an *already-declared* class hash via this path, but does not prevent first-time declaration of an arbitrary new class hash through the bootstrap route at any point in the chain's lifetime, which could differ from what an honest node — using only the `is_authorized_declarer` gate as its trust boundary — expects.

### Likelihood Explanation
Likelihood is **uncertain and could not be fully confirmed** with the tools available:
- I could not verify whether `is_authorized_declarer` or another out-of-band check (e.g., restricting declares to `block_number == 0`, or blacklisting the literal bootstrap address for external RPC submission) prevents this path from being triggered outside genesis bootstrapping. If such a guard exists elsewhere in `apollo_gateway_config` (declarer allow-list defaults) or in `mempool`/`stateful_transaction_validator`, the attack surface would be closed or reduced to configurations that explicitly permit the bootstrap address.
- The `apollo_integration_tests/tests/bootstrap_declare.rs` test explicitly notes the transaction "does not increment its nonce" and stays in the mempool until "rejected during a subsequent attempt," which suggests the mempool/validator do have *some* nonce-based re-entry handling for this special case, but this does not by itself prevent a first use on a live, non-genesis chain.

Given this uncertainty, I flag this as a **credible but unconfirmed** finding requiring code-level confirmation of (a) the default value and enforcement point of `is_authorized_declarer` for the literal bootstrap address, and (b) whether any block-height/one-time-use gate restricts the OS-level bootstrap-declare bypass to genesis only.

### Recommendation
- Confirm whether `is_authorized_declarer` (and equivalent mempool/stateful-validator checks) explicitly deny the hardcoded bootstrap address (`0x424f4f545354524150`) once the network has left genesis/bootstrap phase.
- If no such restriction exists, add an explicit, enforced condition (e.g., "only accepted when `block_number == 0`" or "only accepted from a pre-configured, non-guessable operator identity") both in the gateway's `check_declare_permissions` and in the Starknet OS's `execute_declare_transaction`/blockifier `run_execute` path, so the bypass cannot be triggered by ordinary declare submissions after genesis.
- Add a regression test that submits a bootstrap-shaped declare transaction (`sender_address == bootstrap_address()`, `nonce == 0`, zero fee bounds) against a non-genesis block/state and asserts it is rejected.

### Proof of Concept
Conceptual PoC (based on existing test utilities, not independently executed here):
1. Construct an RPC `Declare` V3 transaction using `sender_address = DeclareTransaction::bootstrap_address()` (the constant `0x424f4f545354524150`), `nonce = Nonce::ZERO`, and `resource_bounds` set so that `compute_max_possible_fee(tx_info) == 0` (i.e., `ValidResourceBounds::create_for_testing_no_fee_enforcement()`), exactly as done by `generate_bootstrap_declare()`: [6](#0-5) 
2. Submit it to the gateway on a live (non-genesis) chain.
3. If `is_authorized_declarer` does not explicitly reject the bootstrap constant, the transaction passes `check_declare_permissions`, reaches the blockifier/OS execution path, and — per `execute_declare_transaction` — the class is declared with no `__validate_declare__` call, no fee charged, and no nonce increment: [2](#0-1) 

This would need to be validated against the actual `is_authorized_declarer` default/config (not found in the indexed context) before being treated as confirmed-exploitable; I recommend a Devin session with full repository access to trace `is_authorized_declarer`'s implementation and default configuration to close this gap.

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

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L945-990)
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
