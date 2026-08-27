### Title
Failed-load fee-only nonce transactions under-report `loaded_accounts_data_size` via `RollbackAccounts::data_size()`, bypassing per-byte cost metering - ([File: svm/src/account_loader.rs])

### Summary
When a transaction fails during account **loading** (not execution) and is committed as a `FeesOnlyTransaction`, the reported `loaded_accounts_data_size` used for cost-model accounting falls back to `RollbackAccounts::data_size()` (sum of nonce + fee-payer data lengths) instead of the actual bytes touched while loading, but only when the `define_ltds_fee_only_semantics` (SIMD-0186 amendment) feature is inactive. Since the nonce and fee-payer accounts are always small (fee payer is system-owned, nonce state is fixed-size), this lets an attacker cause the validator to do real work reading/copying a large auxiliary account before hitting `MaxLoadedAccountsDataSizeExceeded`, while the committed cost accounting reports a near-zero size.

### Finding Description
In `load_transaction` [1](#0-0) , when `load_transaction_accounts` fails (e.g. `MaxLoadedAccountsDataSizeExceeded`, `ProgramAccountNotFound`, etc.), the transaction converts to `FeesOnlyTransaction`. Its `loaded_accounts_data_size` field is set to the actual computed size (`loaded_transaction_data_size`, which correctly enforces the caller's declared limit incrementally via `increase_calculated_data_size` [2](#0-1) ) **only if** `feature_set.define_ltds_fee_only_semantics` is active. Otherwise it falls back to `tx_details.rollback_accounts.data_size() as u32`.

`RollbackAccounts::data_size()` simply sums the data lengths of the nonce and/or fee-payer accounts [3](#0-2) ; a code comment explicitly documents this is a stopgap being replaced by SIMD-186: "used internally when calculating the actual loaded transaction data size for the cost model. This function will be removed by the fee-payer data size amendment to SIMD-186" [4](#0-3) .

Since the fee payer must be system-owned and the nonce account has a small fixed size, `RollbackAccounts::data_size()` is always small and bounded, regardless of what other accounts the attacker's transaction references. Under the old (pre-amendment) semantics, an attacker can include a large writable account in the instruction's account list that pushes the running total past the declared `set_loaded_accounts_data_size_limit`, causing load to fail with `MaxLoadedAccountsDataSizeExceeded` after the validator has already read/copied that large account's data. The transaction then commits as fees-only with a reported `loaded_accounts_data_size` equal to just the tiny nonce+fee-payer size — not the actual (larger) bytes the validator processed — feeding an under-priced cost into `CostModel::calculate_loaded_accounts_data_size_cost` and the block/account cost tracker [5](#0-4) .

This exact behavior difference (old vs. SIMD-186 semantics) is explicitly tested in `svm/tests/integration_test.rs::fee_only_loaded_transaction_data_size`, where for `define_ltds_fee_only_semantics = false` the failing-limit and failing-program-id cases both report `0` for `loaded_accounts_data_size` regardless of how many/how-large the touched accounts were [6](#0-5) , and `runtime/src/bank/tests.rs::test_load_and_execute_commit_transactions_fees_only` confirms the same discrepancy at the bank level [7](#0-6) .

### Impact Explanation
This is a cost-model/metering-accuracy bug affecting resource accounting for failed-load ("fees-only") transactions, not a fund-theft, lamport-inflation, or consensus-divergence bug: all validators compute the same (incorrect) value deterministically from on-chain state, so there is no fork risk. The concrete impact is that an attacker can under-price the per-byte "loaded accounts data size" cost component for transactions that intentionally fail during loading, degrading the accuracy of block/account cost accounting used by `CostTracker::would_fit` and banking-stage scheduling. This falls under "degraded metering / cost-model bypass enabling under-priced resource consumption," a lower-severity DoS/resource-hygiene category rather than the top-tier fund-loss/consensus categories.

### Likelihood Explanation
Preconditions: a funded keypair, a durable nonce account, and a `set_loaded_accounts_data_size_limit` set below the size of one additional large account the attacker references. The attacker needs the `define_ltds_fee_only_semantics` feature to be inactive on the target cluster; the feature exists in this codebase specifically to close this gap (SIMD-0186 amendment), indicating it is a known, already-remediated issue that ships disabled until activated via cluster governance. The magnitude of underpricing per transaction is bounded by the size of a single additional account (up to `MAX_PERMITTED_DATA_LENGTH`), and the attacker still pays base transaction fees, so the incremental cost to the network per abuse instance is limited but repeatable at scale (many transactions per block) while the fix is not yet active.

### Recommendation
Activate the `define_ltds_fee_only_semantics` feature (SIMD-0186 amendment) cluster-wide so `FeesOnlyTransaction::loaded_accounts_data_size` always reflects the actual computed `loaded_transaction_data_size` rather than falling back to `RollbackAccounts::data_size()`. Until activation, consider removing/deprecating the legacy fallback branch entirely or capping the fees-only cost report to the declared `loaded_accounts_bytes_limit` observed during the failed load rather than the rollback-account-only size.

### Proof of Concept
Extend `svm/tests/integration_test.rs::fee_only_loaded_transaction_data_size` (or a bank-level test mirroring `test_load_and_execute_commit_transactions_fees_only`) with `define_ltds_fee_only_semantics = false`:
1. Fund a nonce account and fee payer.
2. Build a transaction: `advance_nonce_account`, `set_loaded_accounts_data_size_limit(small)`, then reference one large writable account (e.g. 8 KiB) in a failing/missing-program instruction so load fails with `MaxLoadedAccountsDataSizeExceeded`.
3. Assert the committed `TransactionLoadedAccountsStats::loaded_accounts_data_size` equals only `nonce_size + fee_payer_size` (near-zero), while the actual account data touched (large account + nonce + fee payer) exceeds the declared limit.
4. Repeat with `define_ltds_fee_only_semantics = true` and assert the reported size instead reflects the limit/actual bytes touched, confirming the fix path and demonstrating the discrepancy exists only pre-activation.

### Citations

**File:** svm/src/account_loader.rs (L446-469)
```rust
            match load_result {
                Ok(accounts) => TransactionLoadResult::Loaded(LoadedTransaction {
                    accounts,
                    // Populated after execution by execute_loaded_transaction.
                    touched_flags: Box::default(),
                    fee_details: tx_details.fee_details,
                    rollback_accounts: tx_details.rollback_accounts,
                    compute_budget: tx_details.compute_budget,
                    loaded_accounts_data_size: loaded_transaction_data_size.into(),
                }),
                Err(err) => TransactionLoadResult::FeesOnly(FeesOnlyTransaction {
                    load_error: err,
                    fee_details: tx_details.fee_details,
                    loaded_accounts_data_size: if account_loader
                        .feature_set
                        .define_ltds_fee_only_semantics
                    {
                        loaded_transaction_data_size.into()
                    } else {
                        tx_details.rollback_accounts.data_size() as u32
                    },
                    rollback_accounts: tx_details.rollback_accounts,
                }),
            }
```

**File:** svm/src/account_loader.rs (L488-511)
```rust
    fn increase_calculated_data_size(
        &mut self,
        data_size_delta: usize,
        error_metrics: &mut TransactionErrorMetrics,
    ) -> Result<()> {
        // this branch is unreachable in practice (though not by construction),
        // since it would imply an account >4gb in size
        let Ok(data_size_delta) = u32::try_from(data_size_delta) else {
            self.loaded_accounts_data_size = u32::MAX;
            error_metrics.max_loaded_accounts_data_size_exceeded += 1;
            return Err(TransactionError::MaxLoadedAccountsDataSizeExceeded);
        };

        self.loaded_accounts_data_size = self
            .loaded_accounts_data_size
            .saturating_add(data_size_delta);

        if self.loaded_accounts_data_size > self.requested_loaded_accounts_data_size_limit {
            error_metrics.max_loaded_accounts_data_size_exceeded += 1;
            Err(TransactionError::MaxLoadedAccountsDataSizeExceeded)
        } else {
            Ok(())
        }
    }
```

**File:** svm/src/rollback_accounts.rs (L137-146)
```rust
    // Size of accounts tracked for rollback, used internally when calculating the actual
    // loaded transaction data size for the cost model. This function will be removed by
    // the fee-payer data size amendment to SIMD-186.
    pub(crate) fn data_size(&self) -> usize {
        let mut total_size: usize = 0;
        for (_, account) in self.iter() {
            total_size = total_size.saturating_add(account.data().len());
        }
        total_size
    }
```

**File:** cost-model/src/cost_model.rs (L196-201)
```rust
    pub fn calculate_loaded_accounts_data_size_cost(
        loaded_accounts_data_size: u32,
        _feature_set: &FeatureSet,
    ) -> u64 {
        Self::calculate_pages_cost(Self::calculate_pages_for_bytes(loaded_accounts_data_size))
    }
```

**File:** svm/tests/integration_test.rs (L3743-3768)
```rust
        // blowing limit with define_ltds_fee_only_semantics sets the size to the limit
        // otherwise it is the raw sum of rollback sizes which here is zero
        assert_eq!(
            if define_ltds_fee_only_semantics {
                size_limit
            } else {
                0
            },
            fail_limit_loaded_size,
        );

        let fail_program_id_loaded_size = output.processing_results[2]
            .as_ref()
            .unwrap()
            .loaded_accounts_data_size();

        // violating constraints *after* passing size with define_ltds_fee_only_semantics uses the size
        // otherwise as above it is the raw sum of rollback sizes which here is zero
        assert_eq!(
            if define_ltds_fee_only_semantics {
                loaded_fee_payer_size + other_accounts_size
            } else {
                0
            },
            fail_program_id_loaded_size,
        );
```

**File:** runtime/src/bank/tests.rs (L1897-1909)
```rust
    let mut loaded_accounts_data_size = 0;
    if define_ltds_fee_only_semantics {
        for key in &transaction.message.account_keys {
            if let Some(n) = bank
                .get_account_shared_data(key)
                .map(|(account, _)| account.data().len())
            {
                loaded_accounts_data_size += (n + TRANSACTION_ACCOUNT_BASE_SIZE) as u32
            }
        }
    } else {
        loaded_accounts_data_size = nonce_size as u32;
    }
```
