### Title
Bootstrap-declare sentinel address bypasses all Declare-transaction authorization (signature/validate, fee, nonce) — free, permission-less class declaration - ([File: crates/starknet_api/src/executable_transaction.rs])

### Summary
`DeclareTransaction::is_bootstrap_declare` treats *any* Declare V3 transaction whose `sender_address` equals a hard-coded sentinel value (`'BOOTSTRAP'`, `0x424f4f545354524150`), with `nonce == 0` and `charge_fee == false`, as a special "genesis bootstrap" transaction. For such a transaction, `AccountTransaction::execute_raw` skips `perform_pre_validation_stage` (nonce/fee checks), skips the mandatory `__validate_declare__` authorization entry point, and skips fee charging entirely — it just runs the class-declare side effect. Because the sentinel address is a public constant and any unprivileged sender can put it in `sender_address`, and any never-before-used address has an implicit nonce of `0`, an attacker can forge a Declare transaction that bypasses every authorization/economic control the protocol normally enforces on declares.

### Finding Description
`is_bootstrap_declare` performs its check purely on transaction *content*, not on any cryptographic or protocol-level proof that the caller legitimately controls the "bootstrap" identity: [1](#0-0) 

`AccountTransaction::execute_raw` special-cases this condition and short-circuits the entire account-authorization/fee pipeline, going straight to `run_execute` (which for Declare only writes the class hash to state): [2](#0-1) 

Note specifically that `perform_pre_validation_stage` (nonce check + fee bound checks) is only invoked *after* this branch returns, i.e. it is never reached for a bootstrap declare: [3](#0-2) [4](#0-3) 

The gateway's only declare-specific authorization gate, `check_declare_permissions`, is an *operator-configured allow-list* (`is_authorized_declarer`) that is optional and, when unset (the permissive/default posture), allows any sender address, including the sentinel bootstrap address, through: [5](#0-4) [6](#0-5) 

The gateway's stateful validation path (`run_validate_entry_point`) builds an `AccountTransaction` and calls `StatefulValidator::validate`, which for Declare/DeployAccount transactions calls `self.execute(tx)` — i.e. it runs the exact same `execute_raw` bypass path described above, so the gateway itself will happily accept and admit such a transaction into the mempool without ever invoking `__validate_declare__`: [7](#0-6) [8](#0-7) 

Since the code comment itself documents that nonce is *not* incremented for this flow ("the mempool will propose the same transaction again... execution will fail because the contract has already been declared"), the bootstrap-address account's nonce remains `0` forever, meaning a single attacker-controlled identity (the public sentinel address) can be reused indefinitely for new fee-free, signature-free declares of different classes.

This is structurally the same bug class as CVE-2023-2786: a privileged/authorized action (declaring a class, which normally requires proving control of an account via `__validate_declare__` and paying a fee) is reachable through an alternate code path that fails to re-apply the required permission check, because that path was designed for a different, implicitly-trusted context (chain genesis) but is not restricted to that context at runtime.

### Impact Explanation
- Unauthorized action: any unprivileged transaction sender can perform a "declare" action that should require proof-of-account-ownership (a valid `__validate_declare__` execution) and fee payment — without either.
- Resource-exhaustion / network availability: because nonce is never incremented for the sentinel address, this identity can be reused an unbounded number of times (each with a distinct class) to declare CASM/Sierra classes for free, consuming compiler CPU/memory, class-manager storage, and state growth with zero economic cost, degrading the sequencer's capacity to process legitimate transactions.
- Where `authorized_declarer_accounts` is not configured (permissive default), this is directly reachable via the public RPC by any user with no special privileges.

### Likelihood Explanation
The only gating condition on this path is the `authorized_declarer_accounts` operator configuration in `check_declare_permissions`; when that allow-list is unset/empty (which the code treats as "unrestricted"), the bypass is trivially reachable from an ordinary `add_transaction` RPC call with a crafted `sender_address`, `nonce=0`, and zero/near-zero fee bounds. No stolen keys, special roles, or node compromise are needed — only knowledge of the public sentinel value baked into the binary.

### Recommendation
- Restrict the bootstrap-declare bypass to a protocol-enforced context (e.g., only allowed for the actual genesis block / block number 0, verified against `block_context.block_info_for_execute.block_number`), not merely based on transaction content that any external sender controls.
- Alternatively, remove the sentinel-address bypass from the general transaction-execution path entirely and perform genesis bootstrapping out-of-band (e.g., via a dedicated admin/CLI flow that writes state directly, never through the public gateway/mempool).
- If the bypass must remain reachable via the gateway, make `check_declare_permissions` unconditionally block the bootstrap sentinel address once the chain has left genesis, and make `is_bootstrap_declare` also assert the account nonce equals the *state* nonce recorded on-chain for a truly first-and-only use (or track a persistent "bootstrap consumed" flag rather than relying on the never-incremented nonce).

### Proof of Concept
1. Compute the sentinel sender address: `ContractAddress::from(0x424f4f545354524150_u128)` (`'BOOTSTRAP'`), as returned by `DeclareTransaction::bootstrap_address()`.
2. Craft an `RpcDeclareTransaction::V3` with `sender_address = bootstrap_address()`, `nonce = 0`, and resource bounds low enough that `enforce_fee(...)` evaluates to `false` (`charge_fee = false`), attaching any Sierra class the attacker wants declared.
3. Submit via the public gateway `add_transaction` endpoint. If `config.static_config.authorized_declarer_accounts` is unset (permissive default), `check_declare_permissions` passes.
4. The gateway's stateful validation (`run_validate_entry_point` → `StatefulValidator::validate` → `execute_raw`) detects `is_bootstrap_declare == true` and runs `tx.run_execute` directly, skipping `__validate_declare__`, fee charging, and nonce increment — the transaction is accepted into the mempool and, upon block inclusion, the class is declared in global state at zero cost and without any signature check.
5. Repeat step 2–4 with different class contents; because the nonce for the bootstrap address is never incremented, the same sender/nonce pair can be reused to declare unlimited additional classes for free.

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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L355-372)
```rust
    pub fn perform_pre_validation_stage<S: State + StateReader>(
        &self,
        state: &mut S,
        tx_context: &TransactionContext,
    ) -> TransactionPreValidationResult<()> {
        let tx_info = &tx_context.tx_info;
        Self::handle_nonce(state, tx_info, self.execution_flags.strict_nonce_check)?;

        if self.execution_flags.charge_fee {
            self.check_fee_bounds(tx_context)?;

            verify_can_pay_committed_bounds(state, tx_context).map_err(Box::new)?;
        }

        self.validate_proof_facts(&tx_context.block_context, state)?;

        Ok(())
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

**File:** crates/blockifier/src/transaction/account_transaction.rs (L914-915)
```rust
        // Nonce and fee check should be done before running user code.
        self.perform_pre_validation_stage(state, &tx_context).map_err(Box::new)?;
```

**File:** crates/apollo_gateway/src/gateway.rs (L228-233)
```rust
        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
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

**File:** crates/blockifier/src/blockifier/stateful_validator.rs (L68-82)
```rust
    pub fn perform_validations(&mut self, tx: AccountTransaction) -> StatefulValidatorResult<()> {
        // Deploy account transaction should be fully executed, since the constructor must run
        // before `__validate_deploy__`. The execution already includes all necessary validations,
        // so they are skipped here.
        // Declare transaction should also be fully executed - otherwise, if we only go through
        // the validate phase, we would miss the check that the class was not declared before.
        match tx.tx {
            ApiTransaction::DeployAccount(_) | ApiTransaction::Declare(_) => self.execute(tx),
            ApiTransaction::Invoke(_) => {
                let tx_context = Arc::new(self.tx_executor.block_context.to_tx_context(&tx));
                tx.perform_pre_validation_stage(self.state(), &tx_context)?;
                if !tx.execution_flags.validate {
                    return Ok(());
                }

```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L302-344)
```rust
    #[sequencer_latency_histogram(GATEWAY_VALIDATE_TX_LATENCY, true)]
    async fn run_validate_entry_point(
        &mut self,
        executable_tx: &ExecutableTransaction,
        skip_validate: bool,
    ) -> StatefulTransactionValidatorResult<()> {
        let only_query = false;
        let charge_fee = enforce_fee(executable_tx, only_query);
        let strict_nonce_check = false;
        let execution_flags =
            ExecutionFlags { only_query, charge_fee, validate: !skip_validate, strict_nonce_check };

        let account_tx = AccountTransaction { tx: executable_tx.clone(), execution_flags };

        // Build block context.
        let mut versioned_constants = VersionedConstants::get_versioned_constants(
            self.config.versioned_constants_overrides.clone(),
        );
        // The validation of a transaction is not affected by the casm hash migration.
        versioned_constants.disable_casm_hash_migration();

        let mut block_info = self.gateway_fixed_block_state_reader.get_block_info().await?;
        block_info.block_number = block_info.block_number.unchecked_next();
        let block_context = BlockContext::new(
            block_info,
            self.chain_info.clone(),
            versioned_constants,
            BouncerConfig::max(),
        );

        // Move state into the blocking task and run CPU-heavy validation.
        let state_reader_and_contract_manager = self.take_state_reader_and_contract_manager();

        let cur_span = Span::current();
        #[allow(clippy::result_large_err)]
        tokio::task::spawn_blocking(move || {
            cur_span.in_scope(|| {
                let state = CachedState::new(state_reader_and_contract_manager);
                let mut blockifier_validator = StatefulValidator::create(state, block_context);
                blockifier_validator.validate(account_tx)
            })
        })
        .await
```
