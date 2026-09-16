## Title
Anyone Can Trigger the Free "BOOTSTRAP" Declare Path to Declare Contract Classes Without Fee, Signature Validation, or Nonce Consumption - (File: `crates/starknet_api/src/executable_transaction.rs`, `crates/blockifier/src/transaction/account_transaction.rs`)

### Summary
A privileged bypass path intended only for genesis/system bootstrapping is gated solely by transaction-content conditions that any unprivileged sender can construct — a publicly-known fixed sender address, `nonce == 0`, and `charge_fee == false`. There is no actual privilege check (e.g., verifying the chain is at genesis, or that the caller is the deployment/bootstrap process). This is directly analogous to the Palmera `removeSafe()` finding, where a function meant to be restricted to a privileged caller (`IsRootSafe`) instead used a weaker check (`SafeRegistered`) that any registered actor could satisfy.

### Finding Description
`DeclareTransaction::bootstrap_address()` returns a fixed, public constant (`ContractAddress::from(0x424f4f545354524150_u128)`, the felt encoding of the string `'BOOTSTRAP'`): [1](#0-0) 

`is_bootstrap_declare()` determines eligibility for this special, unauthenticated execution path purely from transaction fields controlled by the sender: `sender_address == bootstrap_address()`, `nonce == 0`, and `!charge_fee`: [2](#0-1) 

In `AccountTransaction::execute_raw`, if `is_bootstrap_declare()` returns true, the transaction entirely skips `__validate_declare__` execution, nonce increment/consumption, and fee charging/verification — it only performs the class declaration: [3](#0-2) 

Because this path bypasses `perform_pre_validation_stage` (which performs `handle_nonce` and `check_fee_bounds`), the account nonce for the `BOOTSTRAP` address is never incremented. The comment on the integration test confirms this is by design: "Bootstrap declare txs are unique: they are sent from a special address and do not increment its nonce": [4](#0-3) 

The gateway's only declare-related access control is `check_declare_permissions`, which enforces `is_authorized_declarer`: [5](#0-4) 

But `authorized_declarer_accounts` defaults to `None`, and `is_authorized_declarer` returns `true` for any address when unset: [6](#0-5) [7](#0-6) 

The stateful nonce validator only rejects a declare if `incoming_tx_nonce != account_nonce`; since the `BOOTSTRAP` account's nonce is permanently stuck at `0` (never incremented), an attacker-crafted tx with `nonce = 0` always passes: [8](#0-7) 

All three conditions required by `is_bootstrap_declare` (`sender_address`, `nonce`, and resource bounds that yield `charge_fee == false` via `enforce_fee`) are fields directly supplied by the RPC transaction sender — none require any protocol-level privilege, signature ownership of the `BOOTSTRAP` address, or genesis-only gating (e.g., a check on `block_number == 0`).

### Impact Explanation
Any unprivileged party can submit a standard `DECLARE` V3 transaction (reachable directly from the gateway/RPC ingestion path, a single submitted transaction) that:
- Skips `__validate_declare__` entirely (no signature/authorization check of any kind is performed for this path since there is no real account, only a fixed constant address).
- Skips fee charging (`charge_fee` is derived from resource bounds the caller sets, e.g. `create_for_testing_no_fee_enforcement`/zero-fee resource bounds).
- Skips nonce consumption, allowing the exploit to be repeated indefinitely (every retry uses `nonce = 0` again, and the check always passes because the nonce never advances).

This allows unlimited, free `DECLARE` transactions bypassing the gateway's fee-based resource accounting and the operator-configured `authorized_declarer_accounts` allowlist (when unset, which is the default), causing resource exhaustion for Sierra→CASM compilation, class storage bloat, and complete circumvention of fee/resource-based DoS protection intended for all account transactions. This is a concrete unauthorized-action / free-resource-consumption vulnerability reachable from a single unprivileged transaction, matching the "no fee enforcement, unauthorized account action" pattern.

### Likelihood Explanation
High. `bootstrap_address()` is a public, static constant computable by anyone from the source code. Constructing a matching `DECLARE` V3 transaction requires no cryptographic secret, no special network position, and no elevated privilege — only setting three transaction fields to specific, known values. The default gateway configuration (`authorized_declarer_accounts: None`) does not block this address.

### Recommendation
Restrict the bootstrap declare bypass to true genesis conditions rather than transaction-content matching alone:
- Gate `is_bootstrap_declare` (or its invocation site in `AccountTransaction::execute_raw`) on an explicit protocol/genesis state (e.g., `block_context.block_info.block_number == BlockNumber(0)` and/or a one-time "bootstrap completed" flag persisted in state), not solely on sender address/nonce/fee fields chosen by the transaction sender.
- Alternatively/additionally, reject `DECLARE` transactions with `sender_address == bootstrap_address()` at the gateway for any block after genesis, independent of the `authorized_declarer_accounts` configuration.
- Ensure `authorized_declarer_accounts`, when configured, explicitly excludes or specially handles the bootstrap address so operators cannot be silently bypassed by this special-cased path.

### Proof of Concept
1. Craft an RPC `DECLARE` V3 transaction with:
   - `sender_address = DeclareTransaction::bootstrap_address()` (public constant `0x424f4f545354524150`).
   - `nonce = Nonce(Felt::ZERO)`.
   - `resource_bounds` set such that `enforce_fee(&tx, false)` evaluates to `false` (e.g., `ValidResourceBounds::create_for_testing_no_fee_enforcement()`, mirrored in `generate_bootstrap_declare()`): [9](#0-8) 
   - `signature = TransactionSignature::default()` (empty; no validation is performed for this path).
2. Submit via the gateway `add_tx` — passes `check_declare_permissions` by default (`authorized_declarer_accounts = None`).
3. Passes stateful nonce validation because `account_nonce == 0 == incoming_tx_nonce`.
4. At execution, `AccountTransaction::execute_raw` detects `is_bootstrap_declare == true`, skips validation/fee/nonce handling, and declares the class for free.
5. Because the nonce for the bootstrap address is never incremented, repeat steps 1–4 indefinitely with different `class_hash` values to declare unlimited classes for free, at any point after genesis — not just during initial bootstrap.

### Citations

**File:** crates/starknet_api/src/executable_transaction.rs (L246-264)
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

**File:** crates/apollo_integration_tests/tests/bootstrap_declare.rs (L19-22)
```rust
/// Bootstrap declare txs are unique: they are sent from a special address and do not increment its
/// nonce. As a result, they are not removed from the mempool upon successful execution, and will
/// only be removed after being rejected during a subsequent attempt.
#[tokio::test(flavor = "multi_thread", worker_threads = 3)]
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
