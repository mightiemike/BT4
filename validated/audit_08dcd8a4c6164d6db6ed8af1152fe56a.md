### Title
Storage-stake check at transaction admission uses the pre-action storage usage, permanently blocking accounts from fixing their own under-collateralized (LackBalanceForState) state - ([File: runtime/runtime/src/verifier.rs])

### Summary
`check_storage_stake` is invoked during transaction admission (`verify_and_charge_tx_ephemeral`, `verify_and_charge_bootstrap_tx_ephemeral`, and the gas-key deposit path) using the account's *current* `storage_usage()`, i.e. the value **before** any of the transaction's own actions execute. If an account's balance has dropped below the amount needed to back its existing storage usage (a `LackBalanceForState` account), any transaction it signs is rejected at this pre-check — including a `DeleteKey`/`DeleteAccount`/contract-storage-cleanup transaction whose whole purpose is to *reduce* `storage_usage` and restore compliance. This mirrors the reported Perennial pattern of checking the stale, pre-reduction state instead of the state that would result from the very action meant to fix it.

### Finding Description
`check_storage_stake` computes `required_amount = storage_amount_per_byte * account.storage_usage()` and compares it against the account's balance post-fee-debit, but pre-action-execution: [1](#0-0) 

It is called from `verify_and_charge_tx_ephemeral` with `new_amount` (balance minus the transaction's fees only, not reflecting any storage the transaction's own actions would free) and `account`, whose `storage_usage()` is still the pre-action value: [2](#0-1) 

The same pattern repeats in the bootstrap/state-init path: [3](#0-2) 

and in the gas-key deposit path: [4](#0-3) 

This admission-time check happens strictly before any receipt is created or any action is executed, as documented: the runtime "Checks whether the signer account has insufficient balance for the storage deposit and throws `InvalidTxError::LackBalanceForState`" as one of the ordinary transaction-admission checks, prior to receipt creation: [5](#0-4) 

There is no special-casing in `verify_and_charge_tx_ephemeral` (or any sibling verifier function) that exempts, or that pre-simulates, storage-reducing actions such as `DeleteKey`, `DeleteAccount`, or a contract call that clears its own key-value storage. Consequently, once an account's `amount + locked` falls below `storage_amount_per_byte * storage_usage()` (e.g., after being partially slashed, or after storage prices/usage changed, or simply after paying fees), *every* subsequent transaction from that account — including the one action type designed to shrink its footprint back into compliance — is rejected with `InvalidTxError::LackBalanceForState` at admission time, exactly as demonstrated by the existing unit test: [6](#0-5) 

Documentation of the intended state-stake model even flags this failure mode explicitly: "Account can end up with not enough balance in case it gets slashed. Account will become unusable as all originating transactions will fail (including deletion). The only way to recover it in this case is by sending extra funds from a different account." [7](#0-6) 

This confirms the check is a blanket pre-action balance/usage gate with no allowance for the transaction's own state-shrinking effect — the direct analog of the Perennial `Market#_invariant` bug, where a position-reducing action was rejected because the invariant checked the pre-reduction (stale) collateralization instead of the post-reduction one.

### Impact Explanation
An account whose balance is insufficient to back its current `storage_usage()` is permanently frozen with respect to self-help: it cannot send `DeleteKey`, `DeleteAccount`, or any contract call (including one that would delete stored data to shrink `storage_usage`) to bring itself back into compliance, because every such transaction is rejected at admission before its actions ever run. The only stated remedy is an external account depositing more funds — the account cannot resolve its own under-collateralization by reducing its footprint, which is a legitimate and expected user action analogous to reducing an under-margined position. This is a form of permanently frozen self-remediation for the affected account (funds/state become unusable by the account owner without third-party rescue).

### Likelihood Explanation
This requires no adversarial peer, validator, or network condition — it is triggered purely by a single account's own balance/storage state (e.g., after slashing, after storage-cost parameter changes via a protocol upgrade, or simply by an account with many access keys/records whose balance was drawn down by fees) reaching the point where `amount + locked < storage_amount_per_byte * storage_usage()`. Any unprivileged signer of that account transaction will observe this behavior deterministically.

### Recommendation
When the transaction contains actions that are known to strictly reduce `storage_usage` for the signer account (e.g., `DeleteKey`, `DeleteAccount`), simulate/apply the storage-usage delta of those actions before evaluating `check_storage_stake` at admission time, or defer the storage-stake enforcement for such transactions to post-execution (as already done for receipt-level enforcement at `runtime/runtime/src/lib.rs:1009-1031`) rather than gating admission on the pre-action `storage_usage()`. At minimum, exempt transactions whose only actions are storage-reducing (`DeleteKey`/`DeleteAccount`) from the pre-execution `check_storage_stake` gate, mirroring the post-execution check that already uses the correct (post-action) account state.

### Proof of Concept
1. Create an account with several access keys such that `storage_usage()` requires more balance than the account currently has (as in the existing test `test_validate_transaction_invalid_low_balance_many_keys`, `runtime/runtime/src/verifier.rs:1643-1693`), reaching `LackBalanceForState`.
2. From that account, sign a `DeleteKey` transaction removing one or more access keys — an action that, if executed, would reduce `storage_usage()` enough to satisfy `check_storage_stake`.
3. Submit the transaction; `verify_and_charge_tx_ephemeral` computes `check_storage_stake(account, new_amount, config)` using the account's *current* (pre-`DeleteKey`) `storage_usage()` and rejects the transaction with `InvalidTxError::LackBalanceForState` before the `DeleteKey` action ever executes.
4. The account remains stuck in `LackBalanceForState` indefinitely — no self-issued transaction, including the corrective `DeleteKey`/`DeleteAccount`, can ever be admitted, matching the documented failure mode in `docs/Economics/Economics.md:100-101`.

### Citations

**File:** runtime/runtime/src/verifier.rs (L48-63)
```rust
pub fn check_storage_stake(
    account: &Account,
    account_balance: Balance,
    runtime_config: &RuntimeConfig,
) -> Result<(), StorageStakingError> {
    let billable_storage_bytes = account.storage_usage();
    let required_amount = runtime_config
        .storage_amount_per_byte()
        .checked_mul(u128::from(billable_storage_bytes))
        .ok_or_else(|| {
            format!(
                "Account's billable storage usage {} overflows multiplication",
                billable_storage_bytes
            )
        })
        .map_err(StorageStakingError::StorageError)?;
```

**File:** runtime/runtime/src/verifier.rs (L353-386)
```rust
    let available_balance = account.amount().saturating_sub(pending.paid_from_balance);
    if available_balance < total_cost {
        return TxVerdict::Failed(InvalidTxError::NotEnoughBalance {
            signer_id: account_id.clone(),
            balance: available_balance,
            cost: total_cost,
        });
    }
    // Debit only this tx's cost, not the pending amount (which was already
    // charged in prior chunks and will be applied at execution time).
    let new_amount = account.amount().checked_sub(total_cost).unwrap();

    let new_allowance = match check_and_compute_new_allowance(
        access_key,
        account_id,
        tx.public_key(),
        total_cost,
    ) {
        Ok(a) => a,
        Err(e) => return TxVerdict::Failed(e),
    };

    match check_storage_stake(account, new_amount, config) {
        Ok(()) => {}
        Err(StorageStakingError::LackBalanceForStorageStaking(amount)) => {
            return TxVerdict::Failed(InvalidTxError::LackBalanceForState {
                signer_id: account_id.clone(),
                amount,
            });
        }
        Err(StorageStakingError::StorageError(err)) => {
            return TxVerdict::Failed(StorageError::StorageInconsistentState(err).into());
        }
    };
```

**File:** runtime/runtime/src/verifier.rs (L480-504)
```rust
    let available_balance = account.amount().saturating_sub(pending.paid_from_balance);
    if available_balance < total_cost {
        return TxVerdict::Failed(InvalidTxError::NotEnoughBalance {
            signer_id: account_id.clone(),
            balance: available_balance,
            cost: total_cost,
        });
    }
    let new_amount = account.amount().checked_sub(total_cost).unwrap();

    // Vacuous today, since an uninitialized account is always under the
    // zero-balance limit, but it is the same invariant the other paths hold and
    // the account's storage usage is not fixed by anything here.
    match check_storage_stake(account, new_amount, config) {
        Ok(()) => {}
        Err(StorageStakingError::LackBalanceForStorageStaking(amount)) => {
            return TxVerdict::Failed(InvalidTxError::LackBalanceForState {
                signer_id: account_id.clone(),
                amount,
            });
        }
        Err(StorageStakingError::StorageError(err)) => {
            return TxVerdict::Failed(StorageError::StorageInconsistentState(err).into());
        }
    };
```

**File:** runtime/runtime/src/verifier.rs (L644-675)
```rust
    let available_balance = account.amount().saturating_sub(pending.paid_from_balance);
    if available_balance < deposit_cost {
        return TxVerdict::DepositFailed {
            result: make_deposit_failed_result(account.amount()),
            error: InvalidTxError::NotEnoughBalanceForDeposit {
                signer_id: account_id.clone(),
                balance: available_balance,
                cost: deposit_cost,
                reason: DepositCostFailureReason::NotEnoughBalance,
            },
        };
    }
    // Debit only this tx's deposit cost, not the pending amount.
    let new_account_amount = account.amount().checked_sub(deposit_cost).unwrap();

    match check_storage_stake(account, new_account_amount, config) {
        Ok(()) => {}
        Err(StorageStakingError::LackBalanceForStorageStaking(amount)) => {
            return TxVerdict::DepositFailed {
                result: make_deposit_failed_result(account.amount()),
                error: InvalidTxError::NotEnoughBalanceForDeposit {
                    signer_id: account_id.clone(),
                    balance: new_account_amount,
                    cost: amount,
                    reason: DepositCostFailureReason::LackBalanceForState,
                },
            };
        }
        Err(StorageStakingError::StorageError(err)) => {
            return TxVerdict::Failed(StorageError::StorageInconsistentState(err).into());
        }
    };
```

**File:** runtime/runtime/src/verifier.rs (L1670-1693)
```rust
        let err = validate_verify_and_charge_transaction(
            &config,
            &mut state_update,
            signed_tx,
            gas_price,
            None,
            PROTOCOL_VERSION,
        )
        .expect_err("expected an error");
        let account = get_account(&state_update, &account_id).unwrap().unwrap();

        assert_eq!(
            err,
            InvalidTxError::LackBalanceForState {
                signer_id: account_id,
                amount: config
                    .storage_amount_per_byte()
                    .checked_mul(u128::from(account.storage_usage()))
                    .unwrap()
                    .checked_sub(initial_balance.checked_sub(transfer_amount).unwrap())
                    .unwrap()
            }
        );
    }
```

**File:** docs/RuntimeSpec/Scenarios/FinancialTransaction.md (L87-98)
```markdown
The first two items are performed inside `Runtime::verify_and_charge_transaction` method.
Specifically it does the following checks:

- Verifies that the signature of the transaction is correct based on the transaction hash and the attached public key;
- Retrieves the latest state of the `alice_near` account, and simultaneously checks that it exists;
- Retrieves the state of the access key of that `alice_near` used to sign the transaction;
- Checks that transaction nonce is greater than the nonce of the latest transaction executed with that access key;
- Subtracts the `total_cost` of the transaction from the account balance, or throws `InvalidTxError::NotEnoughBalance`. If the transaction is part of a transaction by a FunctionCall Access Key, subtracts the `total_cost` from the `allowance` or throws `InvalidAccessKeyError::NotEnoughAllowance`;
- Checks whether the `signer` account has insufficient balance for the storage deposit and throws `InvalidTxError::LackBalanceForState` if so
- If the transaction is part of a transaction by a FunctionCall Access Key, throws `InvalidAccessKeyError::RequiresFullAccess`;
- Updates the `alice_near` account with the new balance and the used access key with the new nonce;

```

**File:** docs/Economics/Economics.md (L100-101)
```markdown
Account can end up with not enough balance in case it gets slashed. Account will become unusable as all originating transactions will fail (including deletion).
The only way to recover it in this case is by sending extra funds from a different accounts.
```
