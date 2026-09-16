### Title
Unrestricted "BOOTSTRAP" declare backdoor allows unprivileged users to declare classes for free indefinitely — ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo])

### Summary
The Starknet OS declare-transaction handler contains a special-case "bootstrap" path meant only for genesis-time system initialization. This path skips fee charging, `__validate_declare__` execution, and nonce incrementing whenever a `Declare` V3 transaction is sent from a magic sender address (`'BOOTSTRAP'`) with `nonce == 0` and zero possible fee. Nothing in the reachable transaction-processing pipeline (gateway, mempool, blockifier, or the OS itself) restricts this bypass to genesis or to a privileged caller — any unprivileged user can craft such a transaction at any point in the chain's life and repeatedly declare classes for free.

### Finding Description
The bootstrap declare branch lives in `execute_declare_transaction`: [1](#0-0) 

When `sender_address == 'BOOTSTRAP'`, `tx_info.nonce == 0`, `tx_info.version == 3`, and `max_possible_fee == 0`, the OS declares the class and returns immediately via `%{ SkipTx %}`, entirely skipping `check_and_increment_nonce`, the `__validate_declare__` call, and `charge_fee`. The Rust-side mirror of this concept is `DeclareTransaction::is_bootstrap_declare` / `bootstrap_address`: [2](#0-1) 

The `bootstrap_address()` is a fixed, publicly known felt (`0x424f4f545354524150`, ASCII "BOOTSTRAP"). Since it is just a `ContractAddress` value, any external caller can set it as the `sender_address` field of a `DeclareTransaction::V3` they construct themselves — no private key, deployment, or special permission is required to "own" this address, because the bootstrap path never runs `__validate_declare__` (there is no account contract enforcing a signature check).

Critically, because the nonce is never incremented for this special path, the same transaction template (`nonce = 0`) can be resubmitted forever, as acknowledged in the test suite: [3](#0-2) 

At the gateway layer, the only mechanism that could restrict declares to specific senders is `authorized_declarer_accounts`, which defaults to `None` (i.e., unrestricted — every sender, including the bootstrap address, is authorized): [4](#0-3) [5](#0-4) 

The stateful nonce validator for `Declare` only enforces nonce continuity relative to the account's on-chain nonce (`reject_future_declare_txs`); since the bootstrap sender's on-chain nonce never advances, a resubmitted `nonce = 0` bootstrap declare will always satisfy this check: [6](#0-5) 

No component in the reachable path (`apollo_gateway` → mempool → `blockifier`/OS) restricts the bootstrap declare flow to a genesis-only window or to a privileged operator. This is architecturally analogous to the reported `PassThroughWalletImpl.initialize()` bug: a "special initialization" code path intended to run once, under trusted conditions, is left reachable by anyone, at any time, because the only implicit "access control" is a hardcoded address rather than an actual permission check tied to system state (e.g., "has genesis already occurred").

### Impact Explanation
Any unprivileged transaction sender can submit `Declare` transactions using the bootstrap sender address with zero resource bounds, causing the sequencer to:
- Declare arbitrary classes for free, bypassing the entire fee-charging and account-validation mechanism (unauthorized action outside the normal account-abstraction model).
- Trigger unlimited Sierra-to-CASM compilations (CPU/memory intensive, performed pre-fee-verification per the gateway's own comments about `DEFAULT_MAX_CONCURRENT_DECLARE_COMPILATIONS`), and consume declare-related bouncer/block-building capacity — for free, repeatedly, since the sender's nonce is never incremented and thus never exhausted.
- This constitutes a network resource-exhaustion vector reachable via ordinary transaction submission, using zero-fee, one-off transactions that can be replayed indefinitely, potentially crowding out legitimate declare transactions and contributing to a network unable to confirm new transactions at the expected rate.

### Likelihood Explanation
High reachability: the attack requires only constructing a standard `Declare` V3 transaction with a specific (public, hardcoded) sender address, `nonce = 0`, and zero resource bounds — no privileged access, special key, or contract deployment needed. Default gateway configuration (`authorized_declarer_accounts = None`) does not block this. The only gating conditions (`nonce==0`, `version==3`, `max_possible_fee==0`) are all attacker-controlled and satisfied trivially, and can be repeated indefinitely because the nonce is never bumped.

### Recommendation
Restrict the bootstrap declare path so it can only be exercised during genesis/system bootstrap (e.g., gate it on total declared-class count being zero, a dedicated one-time "genesis completed" flag in committed state, or a block-number/height check), rather than solely on a hardcoded sender address and zero nonce. Additionally, ensure the gateway's authorized-declarer allowlist explicitly excludes (or the OS separately validates) the bootstrap address outside of the legitimate bootstrap window, and consider incrementing/consuming the bootstrap sender's nonce (or adding a rate limit / one-shot marker) so the free-declare path cannot be replayed after first use.

### Proof of Concept
1. Attacker crafts a `DeclareTransactionV3` with:
   - `sender_address = ContractAddress::from(0x424f4f545354524150)` (`DeclareTransaction::bootstrap_address()`),
   - `nonce = Nonce(Felt::ZERO)`,
   - `resource_bounds` set via `ValidResourceBounds::create_for_testing_no_fee_enforcement()` (or any bounds yielding `max_possible_fee == 0`),
   - arbitrary `class_hash` / `compiled_class_hash` for a class they want declared.
2. Submits it via the gateway. With default config (`authorized_declarer_accounts = None`), `check_declare_permissions` passes; stateful nonce validation passes because the bootstrap account's on-chain nonce is (and remains) `0`.
3. The Starknet OS executes `execute_declare_transaction`, hits the `sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3` branch, and declares the class for free, skipping `__validate_declare__`, fee charge, and nonce increment.
4. Attacker repeats step 1–3 indefinitely (nonce always `0`), declaring unlimited classes for free and consuming sequencer compilation/bouncer resources without cost, as corroborated by the test comment noting the bootstrap tx "will only be removed after being rejected during a subsequent attempt" (i.e., it can be resent freely). [7](#0-6)

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

**File:** crates/apollo_integration_tests/tests/bootstrap_declare.rs (L19-21)
```rust
/// Bootstrap declare txs are unique: they are sent from a special address and do not increment its
/// nonce. As a result, they are not removed from the mempool upon successful execution, and will
/// only be removed after being rejected during a subsequent attempt.
```

**File:** crates/apollo_gateway_config/src/config.rs (L60-76)
```rust
impl Default for GatewayStaticConfig {
    fn default() -> Self {
        Self {
            stateless_tx_validator_config: StatelessTransactionValidatorConfig::default(),
            stateful_tx_validator_config: StatefulTransactionValidatorConfig::default(),
            contract_class_manager_config: ContractClassManagerConfig {
                contract_cache_size: 300,
                ..Default::default()
            },
            chain_info: ChainInfo::default(),
            block_declare: false,
            authorized_declarer_accounts: None,
            max_concurrent_declare_compilations: DEFAULT_MAX_CONCURRENT_DECLARE_COMPILATIONS,
            proof_archive_writer_config: ProofArchiveWriterConfig::default(),
        }
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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L262-271)
```rust
        match executable_tx {
            // Declare transactions must have the same nonce as the account nonce.
            ExecutableTransaction::Declare(_) if self.config.reject_future_declare_txs => {
                if incoming_tx_nonce != account_nonce {
                    return Err(create_error(format!(
                        "Invalid transaction nonce. Expected: nonce = {account_nonce}, got: \
                         {incoming_tx_nonce}."
                    )));
                }
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
