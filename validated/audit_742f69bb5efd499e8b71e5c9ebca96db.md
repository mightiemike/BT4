## Title
Missing sender-blocking check in stateless transaction validation allows any submitted transaction to bypass intended account-level blocks - (File: crates/apollo_gateway/src/stateless_transaction_validator.rs)

### Summary
The gateway's `StatelessTransactionValidator::validate` function contains an explicit acknowledged gap: there is no check that a transaction's sender address is not on a blocked/suspended list before the transaction is admitted into the pipeline (converted, statefully validated, and forwarded to the mempool).

### Finding Description
`StatelessTransactionValidator::validate` runs a fixed sequence of checks (contract address, empty deployment/paymaster data, resource bounds, tx size, DA modes, and declare-specific checks), but explicitly notes the missing sender-blocking mechanism: [1](#0-0) 

Separately, the only sender-permission gate that exists in the gateway (`check_declare_permissions`, using `static_config.block_declare` and `is_authorized_declarer`) is invoked exclusively for `RpcTransaction::Declare` in `add_tx_inner`: [2](#0-1) [3](#0-2) 

This mirrors the structure of the reported bug class: a permission/suspension check exists for one entry point (there: `forceClosePosition` lacking `notSuspended`; here: Invoke/DeployAccount transactions lacking any sender-blocking check), while a parallel code path intentionally enforces it (there: other PartyA actions; here: Declare via `check_declare_permissions`). Any unprivileged transaction sender submitting an `Invoke` or `DeployAccount` transaction through `add_tx`/`add_tx_inner` bypasses sender-level blocking entirely, since `check_declare_permissions` is skipped for non-Declare transactions and `StatelessTransactionValidator::validate` performs no equivalent check for any transaction type.

### Impact Explanation
If sender-blocking/suspension is a security control the sequencer operator relies on (e.g., to prevent an account flagged for abuse, a suspended participant, or a sanctioned address from continuing to submit state-changing transactions), an account that should be blocked can still submit `Invoke` or `DeployAccount` transactions and have them accepted into the mempool and executed, because the only sender-permission gate in the gateway is declare-specific. This allows unauthorized account action inconsistent with the intended access-control design, undermining any funds-freezing or account-restriction policy implemented at the sequencer layer.

### Likelihood Explanation
High likelihood of exploitability if/when the operator turns on sender blocking for anything other than declare: since the check for Invoke/DeployAccount is entirely absent (not merely misconfigured), any address enrolled in a block list would still be able to submit invoke transactions without any special privilege, using the normal `add_tx` RPC entry point available to all unprivileged senders.

### Recommendation
Implement the sender-blocked check as a general stateless validation applied to all transaction types (Declare, Invoke, DeployAccount), not only via the declare-specific `check_declare_permissions` path, and remove the outstanding TODO in `stateless_transaction_validator.rs` by wiring in a real check against the configured blocked/authorized sender lists for every transaction kind in `add_tx_inner`.

### Proof of Concept
1. Operator configures a sender address to be blocked, but the only enforcement mechanism reachable is `check_declare_permissions`, gated behind `if let RpcTransaction::Declare(ref declare_tx) = tx` in `add_tx_inner` (crates/apollo_gateway/src/gateway.rs:228-233).
2. The blocked sender submits an `Invoke` or `DeployAccount` transaction via `Gateway::add_tx`.
3. `add_tx_inner` skips the declare-permission branch (not a Declare tx), then calls `self.stateless_tx_validator.validate(&tx)`, which performs no sender-blocking check as documented by the TODO at stateless_transaction_validator.rs:34.
4. The transaction proceeds through conversion, stateful validation, and is forwarded to the mempool via `self.mempool_client.add_tx(add_tx_args)`, successfully bypassing the intended block.

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L33-43)
```rust
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
