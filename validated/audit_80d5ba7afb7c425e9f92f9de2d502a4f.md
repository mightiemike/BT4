Confirmed: `RpcDeclareTransactionV3.sender_address` at [1](#0-0)  is a plain client-supplied field, returned directly by `calculate_sender_address()` ( [2](#0-1) ) with no cryptographic binding — unlike deploy-account, where the address is derived from a salt/class-hash/calldata commitment. This is exactly the kind of client-controlled selector that mirrors the "client-controlled `loginMethod`" weakness in the report.

### Title
Any Unprivileged Sender Can Trigger Fee-Free, Signature-Free, Post-Genesis Bootstrap Declare via Sender-Address Self-Assignment - (File: crates/starknet_api/src/executable_transaction.rs)

### Summary
The bootstrap-declare fast path — intended only for genesis-time system initialization — is gated solely on transaction *fields* (`sender_address == BOOTSTRAP_ADDRESS`, `nonce == 0`, `charge_fee == false`) rather than on any chain/block-height state or one-time-use flag. Because `sender_address` in a `DECLARE` transaction is an arbitrary client-supplied value with no relation to a signing key, any transaction sender can construct a declare transaction that satisfies these conditions at any point after genesis, bypassing `__validate_declare__` execution and fee charging entirely.

### Finding Description
`DeclareTransaction::is_bootstrap_declare()` defines the bootstrap condition purely from transaction content: [3](#0-2) 

This flag is consumed by the blockifier to skip normal account validation/fee flow, and independently by the Starknet OS Cairo program to skip `__validate_declare__` and fee charging when `sender_address == 'BOOTSTRAP'`, `nonce == 0`, `version == 3`: [4](#0-3) 

The `sender_address` field for a declare transaction is not derived cryptographically (unlike `DeployAccount`, whose address is `calculate_contract_address()` derived from salt/class-hash/calldata) — it is a bare client-supplied `ContractAddress`: [1](#0-0) [2](#0-1) 

Neither the gateway's stateless validator, stateful validator, nor `check_declare_permissions` reject a declare transaction whose `sender_address` equals the reserved bootstrap constant `0x424f4f545354524150` ("BOOTSTRAP") outside of genesis: [5](#0-4) [6](#0-5) 

No block-number or "genesis-only"/one-shot check exists anywhere in the reviewed declare execution path (`try_declare`, `execute_declare_transaction`) — the only state constraint is `prev_value=0` on the `contract_class_changes` dict, which merely prevents redeclaring the *same* class hash twice, not repeated bootstrap-style declares of *different* classes: [7](#0-6) [8](#0-7) 

This chains three weaknesses analogous to the report: (1) a privileged fast-path skipping authorization/fee (no `isAdmin`-equivalent check) reachable via ordinary transaction submission, (2) a special sentinel account (`BOOTSTRAP_ADDRESS`) whose privileged semantics persist indefinitely rather than being retired after genesis (the "orphaned row"), and (3) a client-controlled field (`sender_address`) that selects into this privileged path without any binding to an authenticated identity (the "client-controlled loginMethod").

### Impact Explanation
An attacker submitting an ordinary `DECLARE` transaction with `sender_address = BOOTSTRAP_ADDRESS`, `nonce = 0`, and zero resource bounds/fee gets a class declared into state with **no signature verification and no fee payment**, at any block height, not just genesis. This is a state-integrity violation: sequencer state diverges from what honest gateway-guarded declare flow should allow, effectively an unauthorized privileged action (fee-free declare bypassing account validation) reachable by any unprivileged sender — matching the "unauthorized account action"/"honest-node divergence" impact bar. If nonce management for `BOOTSTRAP_ADDRESS` is not otherwise pinned to genesis, repeated exploitation could allow unlimited free declares from this pseudo-account, and because the OS replicates the same unguarded condition, this would also reproduce during Starknet OS re-execution, causing the OS's committed class-diff/state root to reflect this attacker-triggered path identically to the (buggy) sequencer — i.e., it is not purely a "sequencer bug caught by re-execution" but a systemic protocol-level gap.

### Likelihood Explanation
Reaching this path requires only crafting a standard V3 declare transaction with a specific `sender_address`/`nonce`/fee combination — no special privileges, keys, or race conditions are needed, and the gateway performs no rejection of the sentinel address for non-genesis blocks based on the code reviewed. This gives it high likelihood provided no out-of-band check (e.g., "system already bootstrapped" flag, or genesis-block-only gating enforced elsewhere not covered by the indexed context) exists.

### Recommendation
1. Restrict `is_bootstrap_declare` matching to block height 0 (or an explicit one-time "system bootstrapped" flag in state), not merely transaction field values.
2. Reject any declare/invoke/deploy transaction whose `sender_address` equals `bootstrap_address()` at the gateway (`check_declare_permissions` / `validate_contract_address`) for all blocks after genesis.
3. Ensure the Starknet OS Cairo bootstrap-declare branch enforces the equivalent block-height/one-time gating so sequencer and OS re-execution cannot diverge from the intended semantics.

### Proof of Concept
1. Craft an `RpcDeclareTransaction::V3` with `sender_address = ContractAddress(0x424f4f545354524150)` (the `bootstrap_address()` constant), `nonce = Nonce(0)`, and `resource_bounds` set so `compute_max_possible_fee(tx_info) == 0` (matching `!charge_fee`), with an arbitrary Sierra `contract_class`.
2. Submit it through the gateway `add_tx` RPC entry point as any regular unauthenticated client.
3. Because `check_declare_permissions` only checks `block_declare` and `authorized_declarer_accounts` — the latter defaults to `None`/unset in most configs, per `is_authorized_declarer` — and no genesis-height check exists, the transaction passes stateless/stateful validation.
4. `is_bootstrap_declare(charge_fee=false)` returns `true`; `__validate_declare__` and fee-charging are skipped entirely in both the blockifier and Starknet OS execution path, and the class is declared for free without any signature check.

**Note on completeness:** I was unable to fully verify whether `authorized_declarer_accounts` is enforced to always be `Some(...)` in production deployment configs (some sample deployment JSONs show it templated/possibly `None`), nor whether a separate, unindexed "genesis-only" gate exists elsewhere in the codebase (e.g., in block-building or batcher logic) that I could not locate via the available search tools. This should be verified directly in the full repository before treating this as a confirmed exploitable finding.

### Citations

**File:** crates/starknet_api/src/rpc_transaction.rs (L177-185)
```rust
    pub fn calculate_sender_address(&self) -> Result<ContractAddress, StarknetApiError> {
        match self {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => Ok(tx.sender_address),
            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(tx)) => {
                tx.calculate_contract_address()
            }
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => Ok(tx.sender_address),
        }
    }
```

**File:** crates/starknet_api/src/rpc_transaction.rs (L352-366)
```rust
pub struct RpcDeclareTransactionV3 {
    // TODO(Mohammad): Check with Shahak why we need to keep the DeclareType.
    // pub r#type: DeclareType,
    pub sender_address: ContractAddress,
    pub compiled_class_hash: CompiledClassHash,
    pub signature: TransactionSignature,
    pub nonce: Nonce,
    pub contract_class: SierraContractClass,
    pub resource_bounds: AllResourceBounds,
    pub tip: Tip,
    pub paymaster_data: PaymasterData,
    pub account_deployment_data: AccountDeploymentData,
    pub nonce_data_availability_mode: DataAvailabilityMode,
    pub fee_data_availability_mode: DataAvailabilityMode,
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

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L31-54)
```rust
impl StatelessTransactionValidator {
    #[instrument(skip(self), level = Level::INFO)]
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
        // TODO(Arni, 1/5/2024): Validate transaction version.

        Self::validate_contract_address(tx)?;
        Self::validate_empty_account_deployment_data(tx)?;
        Self::validate_empty_paymaster_data(tx)?;
        self.validate_resource_bounds(tx)?;
        self.validate_tx_size(tx)?;
        self.validate_nonce_data_availability_mode(tx)?;
        self.validate_fee_data_availability_mode(tx)?;

        if let RpcTransaction::Invoke(invoke_tx) = tx {
            self.validate_client_side_proving_allowed(invoke_tx)?;
            self.validate_proof_facts_and_proof_consistency(invoke_tx)?;
        }

        if let RpcTransaction::Declare(declare_tx) = tx {
            self.validate_declare_tx(declare_tx)?;
        }
        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/transactions.rs (L379-409)
```rust
/// Determines whether the fee should be enforced for the given transaction.
pub fn enforce_fee(tx: &AccountTransaction, only_query: bool) -> bool {
    // TODO(AvivG): Consider implemetation without 'create_tx_info'.
    tx.create_tx_info(only_query).enforce_fee()
}

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
