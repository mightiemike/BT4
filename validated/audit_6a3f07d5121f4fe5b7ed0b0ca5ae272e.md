Based on my research, I found a mechanism in the Starknet OS / blockifier declare-transaction flow that mirrors the "Anyone can start an auction" bug class very closely: a permission-gated action (declaring a class "for free," bypassing `__validate_declare__`, fee charging, and nonce incrementing) is gated purely by a **magic sentinel value check** (`sender_address == 'BOOTSTRAP' && nonce == 0 && charge_fee == false`) rather than by an unforgeable, state-tracked "genesis has not yet happened" condition — exactly analogous to `_epoch_in_progress()` trivially evaluating `false` on default/zero state.

### Title
Bootstrap-declare bypass allows unauthorized, fee-free class declarations after genesis if `allow_bootstrap_txs`/`validate_resource_bounds=false` remains enabled - ([File: crates/starknet_api/src/executable_transaction.rs], [File: crates/blockifier/src/transaction/account_transaction.rs], [File: crates/apollo_starknet_os_program/.../transaction_impls.cairo])

### Summary
The declare-transaction path (both the Rust blockifier and the Starknet OS Cairo re-execution) contains a special "bootstrap" bypass that skips `__validate_declare__`, fee charging, and nonce incrementing whenever a declare transaction's `sender_address` equals the fixed constant `bootstrap_address()` (felt encoding of `'BOOTSTRAP'`) and `nonce == 0` and `charge_fee == false`. This mirrors the reported pattern: a state/condition that is trivially satisfiable by an unprivileged caller (a hardcoded address + a nonce that is *never incremented* by this code path) permanently allows a privileged action (free, unvalidated class declaration) to be taken, with no additional binding to "this is genesis" beyond gateway-side configuration.

### Finding Description
`DeclareTransaction::is_bootstrap_declare` treats any declare tx as a no-fee bootstrap declare purely based on sender address, nonce, and the `charge_fee` flag: [1](#0-0) 

`AccountTransaction::execute_raw` in the blockifier honors this flag by skipping `perform_pre_validation_stage` (nonce/fee checks) entirely, and running only the class-declaration side effect: [2](#0-1) 

The Starknet OS program mirrors the same bypass in Cairo, likewise gated only by `sender_address == 'BOOTSTRAP' and tx_info.nonce == 0 and tx_info.version == 3` plus `max_possible_fee == 0`, and explicitly notes "prev_value=0 enforces that a class may be declared only once" — which only prevents re-declaring the *same* class hash, not re-use of the bootstrap identity for *new* class hashes: [3](#0-2) 

Critically, this code path never calls `check_and_increment_nonce`, so the bootstrap account's nonce remains `0` forever — the sentinel condition (`nonce == 0`) can be satisfied indefinitely, not just once at genesis, exactly like `epoch_start == 0` in the audited report remaining permanently exploitable until the auction is actually started.

Whether this is reachable by an ordinary user depends entirely on the gateway-side `validate_resource_bounds` / `allow_bootstrap_txs` configuration, which is wired as a static, node-wide switch: [4](#0-3) 
and used to select between the real state reader and a `GenesisStateReader` only when `latest_block_number` is `None` (i.e., before any block exists): [5](#0-4) 

However, `validate_non_zero_resource_bounds`/`validate_resource_bounds` gating the *stateless* zero-resource-bound rejection is a separate, statically-configured gateway flag, not tied to "has genesis happened" state: [6](#0-5) 
If a deployed network leaves this flag enabled after genesis (which is architecturally required at least transiently to admit the very first bootstrap declare), any transaction sender who can still produce a `sender_address == bootstrap_address(), nonce == 0`, zero-resource-bound declare transaction can reach the same bypass at any later block height — there is no explicit "only at block 0" gate enforced in the OS/blockifier logic itself, only the ephemeral genesis-vs-non-genesis distinction in the gateway's state-reader selection.

### Impact Explanation
If reachable post-genesis, this allows an unprivileged transaction sender to:
- Declare arbitrary new classes without paying any fee (`charge_fee=false`), and without passing `__validate_declare__` (no signature/authorization check on the "account"), which is an unauthorized account action / bypass of the fee-market and declare-transaction authorization model.
- Repeat this for every distinct class hash indefinitely, since the "account" nonce is never bumped by this path, unlike the audited bug's one-shot state transition — this is arguably a stronger, repeatable variant of "anyone can start an auction."
- This directly corresponds to "unauthorized account action" from the validation criteria (declares without validation) and creates a resource/DoS vector on class declaration storage.

### Likelihood Explanation
Likelihood is conditioned on a specific deployment/config state (`validate_resource_bounds=false` / `allow_bootstrap_txs=true` remaining enabled after genesis) which I could not fully verify from the indexed code — I found the config wiring and test harness (`crates/apollo_integration_tests/tests/bootstrap_declare.rs`, `account_transactions_test.rs::test_bootstrap_declare`) confirming the mechanism and its edge-case tests (wrong nonce, wrong sender, non-trivial resource bounds all rejected), but I could not locate a runtime/state-based check (e.g., "only if `latest_block_number == None`") enforced at the blockifier/OS layer itself that would prevent reuse of this bypass after the chain has produced blocks, nor could I fully trace the production default/lifecycle of the `allow_bootstrap_txs` flag (whether it is toggled off automatically after genesis or remains a static per-deployment setting for the node's lifetime). This uncertainty should be resolved by a Devin session with full repository/config access before treating this as confirmed-exploitable in a real deployment.

### Recommendation
Bind the bootstrap-declare bypass to an unforgeable, monotonic state condition (e.g., only permit it while `block_number == 0` / no blocks have yet been committed, checked in the OS/blockifier itself, not only via gateway config), and/or have `check_and_increment_nonce` run even for the bootstrap declare path so the sentinel condition (`nonce == 0`) cannot be satisfied more than once, closing the "anyone can retrigger this indefinitely" gap analogous to the reported auction issue.

### Proof of Concept
Not independently reproducible from static review alone; the existing test `crates/apollo_integration_tests/tests/bootstrap_declare.rs` and `crates/blockifier/src/transaction/account_transactions_test.rs::test_bootstrap_declare` demonstrate the mechanics of the bypass (fee-free, validation-free declare via `sender_address = bootstrap_address()`, `nonce = 0`) but exercise it only under the intentional `allow_bootstrap_txs()`/genesis test configuration; confirming exploitability on a live, post-genesis network requires verifying the actual production default and lifecycle of the `validate_resource_bounds`/`allow_bootstrap_txs` gateway configuration, which I was unable to fully trace within the available index.

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

**File:** crates/apollo_integration_tests/src/utils.rs (L347-363)
```rust
    let validate_non_zero_resource_bounds = !allow_bootstrap_txs;
    let gateway_config =
        create_gateway_config(chain_info.clone(), validate_non_zero_resource_bounds);
    let l1_events_scraper_config = L1EventsScraperConfig {
        chain_id: chain_info.chain_id.clone(),
        startup_rewind_time_seconds: Duration::from_secs(0),
        polling_interval_seconds: Duration::from_secs(1),
        ..Default::default()
    };
    let l1_events_provider_config = L1EventsProviderConfig {
        startup_sync_sleep_retry_interval_seconds: Duration::from_secs(0),
        l1_handler_cancellation_timelock_seconds: Duration::from_secs(0),
        l1_handler_consumption_timelock_seconds: Duration::from_secs(0),
        l1_handler_proposal_cooldown_seconds: Duration::from_secs(0),
        ..Default::default()
    };
    let validate_resource_bounds = !allow_bootstrap_txs;
```

**File:** crates/apollo_gateway/src/sync_state_reader.rs (L528-534)
```rust
        let latest_block_number = self.shared_state_sync_client.get_latest_block_number().await?;

        // If no blocks exist yet, return genesis state readers for bootstrap transactions.
        let Some(latest_block_number) = latest_block_number else {
            info!("No blocks found yet; using genesis state readers for bootstrap transactions.");
            return Ok((GenesisStateReader.into(), GenesisFixedBlockStateReader.into()));
        };
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L223-243)
```rust
    async fn validate_resource_bounds(
        &self,
        executable_tx: &ExecutableTransaction,
    ) -> StatefulTransactionValidatorResult<()> {
        // Skip this validation during the systems bootstrap phase.
        if self.config.validate_resource_bounds {
            // TODO(Arni): getnext_l2_gas_price from the block header.
            let previous_block_l2_gas_price = self
                .gateway_fixed_block_state_reader
                .get_block_info()
                .await?
                .gas_prices
                .strk_gas_prices
                .l2_gas_price;
            self.validate_tx_l2_gas_price_within_threshold(
                executable_tx.resource_bounds(),
                previous_block_l2_gas_price,
            )?;
        }
        Ok(())
    }
```
