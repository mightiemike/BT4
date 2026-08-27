Based on the code, this attack is not feasible. The claimed race condition does not exist because the transaction batch processor executes transactions **strictly sequentially, in transaction order**, and propagates account state changes immediately after each transaction—there is no window where two transactions in the same batch execute concurrently against the same pre-advance nonce state.

### Key evidence against the premise

1. **Sequential execution is explicit and documented.** `TransactionBatchProcessor::load_and_execute_sanitized_transactions` iterates transactions in a single `for` loop, with an explicit comment stating this is required precisely to prevent the scenario the question describes: [1](#0-0) 

2. **Account state is updated in-place after each transaction, before the next one loads accounts.** After a transaction executes, `account_loader.update_accounts_for_successful_tx` (or `update_accounts_for_failed_tx`) immediately writes the resulting account state (including the newly-advanced nonce) into the shared `AccountLoader` cache used by subsequent transactions in the same loop iteration: [2](#0-1) 

3. **The second nonce transaction in the batch loads the already-advanced nonce account.** `validate_transaction_nonce` calls `account_loader.load_transaction_account`, which reads from the same per-batch cache that was just updated by the first transaction. Since the durable nonce has already been advanced to `next_durable_nonce`, the check `nonce_data.durable_nonce != next_durable_nonce` fails, and the transaction is rejected as unprocessable (`TransactionError::BlockhashNotFound`) — it is never re-executed against the stale pre-advance state: [3](#0-2) 

4. **This exact "used-in-batch" scenario is explicitly called out and handled by SIMD-83.** The comment directly above `validate_transaction_nonce` states this was a known concern that is deliberately closed off: [4](#0-3) 

5. **Existing tests already validate this exact scenario and pass with the correct (non-exploitable) outcome.** `test_process_entries_2nd_entry_collision_with_self_and_error` and `test_process_entries_2_txes_collision` in `ledger/src/blockstore_processor.rs` process entries with intra-batch account conflicts and assert deterministic, correct balances — not a double-spend: [5](#0-4) 
Additionally, `test_validate_transaction_nonce` in `svm/src/transaction_processor.rs` has an explicit `AlreadyUsed` case verifying that a second nonce use in the same durable-nonce window is rejected with `BlockhashNotFound`: [6](#0-5) 

6. Regarding the account-locking detail referenced by the question: it is true that `AccountLocks::try_lock_transaction_batch` allows two write-conflicting transactions to both obtain locks *within the same batch call* (this is by design, since intra-batch ordering is handled by sequential execution, not by locks): [7](#0-6) 
This does not create a race because "locking" here only gates against *other, separate* `prepare_sanitized_batch` calls (i.e., concurrent unrelated banking-stage batches); it has no bearing on execution order *within* a single locked batch, which is strictly sequential per point 1 above.

Since the attacker cannot cause two nonce transactions referencing the same nonce/durable-nonce value to both observe pre-advance state — the second transaction in program order always sees the already-advanced nonce and is rejected — there is no reachable double-spend path here.

#No vulnerability found for this question.

### Citations

**File:** svm/src/transaction_processor.rs (L467-471)
```rust
        // Validate, execute, and collect results from each transaction in order.
        // With SIMD83, transactions must be executed in order, because transactions
        // in the same batch may modify the same accounts. Transaction order is
        // preserved within entries written to the ledger.
        for (tx, check_result) in sanitized_txs.iter().zip(check_results) {
```

**File:** svm/src/transaction_processor.rs (L592-620)
```rust
                        // Successful transactions need to update the account loader cache as future
                        // transactions in the batch may depend on them.
                        (Ok(_), _) => {
                            account_loader.update_accounts_for_successful_tx(
                                tx,
                                &executed_tx.loaded_transaction.accounts,
                                &executed_tx.loaded_transaction.touched_flags,
                                self.slot,
                            );
                            // Also update local program cache with modifications made by the
                            // transaction, if it executed successfully.
                            program_cache_for_tx_batch.merge(&executed_tx.programs_modified_by_tx);

                            Ok(ProcessedTransaction::Executed(Box::new(executed_tx)))
                        }
                        // If the transaction failed & drop on failure is set then we don't want to
                        // update the accounts as this transaction will be dropped from the batch.
                        (Err(err), true) => Err(err.clone()),
                        // Unsuccessful transactions will still update rollback accounts (fee payer,
                        // nonce, etc).
                        (Err(_), false) => {
                            account_loader.update_accounts_for_failed_tx(
                                &executed_tx.loaded_transaction.rollback_accounts,
                                self.slot,
                            );

                            Ok(ProcessedTransaction::Executed(Box::new(executed_tx)))
                        }
                    }
```

**File:** svm/src/transaction_processor.rs (L842-846)
```rust
        // When SIMD83 is enabled, if the nonce has been used in this batch already, we must drop
        // the transaction. This is the same as if it was used in different batches in the same slot.
        // It is possible that the nonce account was used, closed, closed and reopened, closed and
        // spoofed by a non-system program, or had its authority changed. Such a transaction cannot
        // be processed, even as fee-only.
```

**File:** svm/src/transaction_processor.rs (L861-891)
```rust
        // This function verifies:
        // * Nonce account owner is SystemProgram
        // * Nonce account parses as State::Initialized
        // * Stored durable nonce matches the message blockhash
        let Some(nonce_data) = verify_nonce_account(&nonce_account, message.recent_blockhash())
        else {
            error_counters.blockhash_not_found += 1;
            return Err(TransactionError::BlockhashNotFound);
        };

        // We must still check that the nonce account is usable and that its authority has signed.
        let nonce_can_be_advanced = &nonce_data.durable_nonce != next_durable_nonce;
        let nonce_authority_is_valid = message
            .get_ix_signers(NONCED_TX_MARKER_IX_INDEX as usize)
            .any(|signer| signer == &nonce_data.authority);

        if nonce_can_be_advanced && nonce_authority_is_valid {
            let next_nonce_state = NonceState::new_initialized(
                &nonce_data.authority,
                *next_durable_nonce,
                next_lamports_per_signature,
            );
            nonce_account
                .set_state(&NonceVersions::new(next_nonce_state))
                .expect("Serializing into a validated nonce account cannot fail");

            Ok(NonceInfo::new(*nonce_address, nonce_account))
        } else {
            error_counters.blockhash_not_found += 1;
            Err(TransactionError::BlockhashNotFound)
        }
```

**File:** svm/src/transaction_processor.rs (L2510-2514)
```rust
        let stored_durable_nonce = if case == ValidateNonce::AlreadyUsed {
            next_durable_nonce
        } else {
            previous_durable_nonce
        };
```

**File:** ledger/src/blockstore_processor.rs (L3708-3726)
```rust
        // succeeds following simd83 locking, fails otherwise
        let result = process_entries_for_tests_with_scheduler(
            &bank,
            vec![
                entry_1_to_mint,
                entry_2_to_3_and_1_to_mint,
                entry_conflict_itself,
            ],
        );

        let balances = [
            bank.get_balance(&keypair1.pubkey()),
            bank.get_balance(&keypair2.pubkey()),
            bank.get_balance(&keypair3.pubkey()),
        ];

        assert!(result.is_ok());
        assert_eq!(balances, [0, 3, 3]);
    }
```

**File:** accounts-db/src/accounts.rs (L1287-1295)
```rust
        // ww conflict in-batch succeeds
        let accounts = Accounts::new(accounts_db);
        let results = accounts.lock_accounts(
            [w_tx, r_tx].iter(),
            [Ok(()), Ok(())].into_iter(),
            MAX_TX_ACCOUNT_LOCKS,
        );

        assert_eq!(results, vec![Ok(()), Ok(())]);
```
