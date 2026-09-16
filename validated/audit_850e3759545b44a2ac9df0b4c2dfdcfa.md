### Title
Gateway skips `__validate__` (signature check) for post-deploy_account transactions based on unverified sender/nonce claims, enabling unauthenticated mempool-slot DoS - (File: crates/apollo_gateway/src/stateful_transaction_validator.rs)

### Summary
`skip_stateful_validations` in the gateway's stateful transaction validator disables signature verification (`execution_flags.validate = false`) for an incoming Invoke transaction whenever its claimed `nonce == 1` and the current on-chain account nonce is `0`, gated only by a check that *some* deploy_account transaction for that sender address exists in the mempool or a recent block. None of these gating facts (sender address, nonce=1) are authenticated by the incoming transaction's signature — they are exactly the kind of caller-supplied claims the ueberauth/guardian analog warns about being trusted before verification. This mirrors `Guardian.revoke/3` acting on `peek`-decoded, unverified JWT claims: here, the gateway performs a mempool-admission (state-mutating) action — inserting the attacker's transaction into the victim account's nonce-1 slot — without ever executing `__validate__`, based solely on unauthenticated claims.

### Finding Description
In `extract_state_nonce_and_run_validations`, the gateway runs `run_pre_validation_checks` to compute `skip_validate`, then calls `run_validate_entry_point(executable_tx, skip_validate)`, which sets `execution_flags.validate = !skip_validate` before invoking `StatefulValidator::validate`: [1](#0-0) 

`skip_stateful_validations` decides to skip validation purely from the (attacker-supplied) `tx.nonce()` and `tx.sender_address()`, checking only that *an* deploy_account tx for that address is present in the mempool/recent block — not that the *specific* invoke transaction being admitted was actually signed by the account: [2](#0-1) 

`run_validate_entry_point` builds `ExecutionFlags { validate: !skip_validate, .. }` and runs `blockifier_validator.validate(account_tx)`; when `validate` is `false`, `__validate__` (the account contract's signature check) is never executed: [3](#0-2) 

The resulting nonce is then handed straight to `mempool_client.add_tx`, mutating mempool state (occupying the nonce=1 slot for that account) with no cryptographic proof the transaction was authorized by the account owner: [4](#0-3) 

This is structurally identical to the reported bug class: a state-mutating operation (`add_tx`/mempool admission ≈ `revoke`) acts on caller-supplied claims (`sender_address`, `nonce`) without verifying the cryptographic proof (`peek` ≈ skipped `__validate__`), while the sibling/normal admission path (regular nonce ranges) does require full `run_validate_entry_point` with `validate = true`.

### Impact Explanation
Any unprivileged sender who observes a victim's `deploy_account` transaction (address is public, broadcast to mempool/L1) can immediately submit an Invoke transaction with `nonce = 1`, the victim's `sender_address`, and an arbitrary/garbage signature. The gateway will skip signature verification for that transaction, and it will be admitted to the mempool occupying the victim's nonce-1 slot before the mempool's own fee-escalation logic (`validate_fee_escalation`/`remove_replaced_tx`) even runs. Because mempool tracks one queued tx per (address, nonce), this blocks the account's legitimate first transaction from being admitted unless it pays a strictly higher tip to displace the forged one, denying/delaying confirmation of the victim's transaction — an unauthenticated DoS against a specific account's transaction confirmation, matching CVSS Medium severity of the underlying bug class.

### Likelihood Explanation
Trivially reachable: no privileges are required, the only precondition is knowledge of a victim address that recently submitted `deploy_account` (visible from the mempool/L2 the moment it's broadcast), and crafting a syntactically valid Invoke transaction with nonce 1 and any signature bytes (stateless validation only checks format, not the signature's cryptographic validity against the account).

### Recommendation
Never derive `skip_validate = true` from unauthenticated request fields alone. At minimum, require the incoming invoke transaction to also pass `__validate__` (or a lightweight signature check comparable to full validation) before admitting it to the mempool nonce slot, or restrict the skip to only apply when the specific transaction's authenticity can be established (e.g., match against the same submitter/session, or perform validation once the corresponding deploy_account confirms on-chain rather than merely "present in mempool").

### Proof of Concept
1. Observe a victim's `deploy_account` transaction for address `A` enter the mempool (or a recent block).
2. Submit an Invoke transaction: `sender_address = A`, `nonce = 1`, arbitrary/garbage `signature`, arbitrary calldata.
3. Gateway's `extract_state_nonce_and_run_validations` computes `account_nonce = 0` for `A`; `skip_stateful_validations` returns `true` because `nonce == 1 && account_nonce == 0` and `account_tx_in_pool_or_recent_block(A)` is `true`.
4. `run_validate_entry_point` runs with `execution_flags.validate = false`, so `__validate__` never checks the forged signature.
5. The forged transaction is forwarded to `mempool_client.add_tx` and occupies `A`'s nonce-1 slot, blocking the legitimate user's real first transaction from being admitted at that nonce.

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L158-179)
```rust
    async fn extract_state_nonce_and_run_validations(
        &mut self,
        executable_tx: &ExecutableTransaction,
        mempool_client: SharedMempoolClient,
    ) -> StatefulTransactionValidatorResult<Nonce> {
        let account_nonce =
            self.get_nonce_from_state(executable_tx.contract_address()).await.map_err(|e| {
                // TODO(noamsp): Fix this. Need to map the errors better.
                StarknetError::internal_with_signature_logging(
                    format!(
                        "Failed to get nonce for sender address {}",
                        executable_tx.contract_address()
                    ),
                    &executable_tx.signature(),
                    e,
                )
            })?;
        let skip_validate =
            self.run_pre_validation_checks(executable_tx, account_nonce, mempool_client).await?;
        self.run_validate_entry_point(executable_tx, skip_validate).await?;
        Ok(account_nonce)
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L303-342)
```rust
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
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L426-461)
```rust
/// Check if validation of an invoke transaction should be skipped due to deploy_account not being
/// processed yet. This feature is used to improve UX for users sending deploy_account + invoke at
/// once.
async fn skip_stateful_validations(
    tx: &ExecutableTransaction,
    account_nonce: Nonce,
    mempool_client: SharedMempoolClient,
) -> StatefulTransactionValidatorResult<bool> {
    if let ExecutableTransaction::Invoke(ExecutableInvokeTransaction { tx, .. }) = tx {
        // check if the transaction nonce is 1, meaning it is post deploy_account, and the
        // account nonce is zero, meaning the account was not deployed yet.
        if tx.nonce() == Nonce(Felt::ONE) && account_nonce == Nonce(Felt::ZERO) {
            let account_address = tx.sender_address();
            debug!("Checking if deploy_account transaction exists for account {account_address}.");
            // We verify that a deploy_account transaction exists for this account. It is sufficient
            // to check if the account exists in the mempool since it means that either it has a
            // deploy_account transaction or transactions with future nonces that passed
            // validations.
            return mempool_client
                .account_tx_in_pool_or_recent_block(tx.sender_address())
                .await
                .map_err(|err| mempool_client_err_to_deprecated_gw_err(&tx.signature(), err))
                .inspect(|exists| {
                    if *exists {
                        debug!("Found deploy_account transaction for account {account_address}.");
                    } else {
                        debug!(
                            "No deploy_account transaction found for account {account_address}."
                        );
                    }
                });
        }
    }

    Ok(false)
}
```

**File:** crates/apollo_gateway/src/gateway.rs (L253-286)
```rust
        let (internal_tx, executable_tx, proof_data) =
            self.convert_rpc_tx_to_internal_and_executable_txs(tx, &tx_signature).await?;
        drop(compilation_permit);

        let mut stateful_transaction_validator = self
            .stateful_tx_validator_factory
            .instantiate_validator(self.config.dynamic_config.native_classes_whitelist.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let nonce = stateful_transaction_validator
            .extract_state_nonce_and_run_validations(&executable_tx, self.mempool_client.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let proof_archive_handle = self
            .store_proof_and_spawn_archiving(proof_data, internal_tx.tx_hash, is_p2p)
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let gateway_output = create_gateway_output(&internal_tx);

        let add_tx_args = AddTransactionArgsWrapper {
            args: AddTransactionArgs::new(internal_tx, nonce),
            p2p_message_metadata,
        };

        // Await as late as possible for proof archiving before sending the transaction to the
        // mempool.
        Self::await_proof_archiving(proof_archive_handle)
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let mempool_client_result = self.mempool_client.add_tx(add_tx_args).await;
```
