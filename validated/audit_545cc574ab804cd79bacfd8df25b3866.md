### Title
Deleting an account with non-zero locked (staked) balance permanently destroys the locked funds - ([File: runtime/runtime/src/actions.rs])

### Summary
`action_delete_account` computes the payout to the `beneficiary_id` using only the account's spendable `amount()`, never its `locked()` (staked) balance, and then unconditionally sets the account to `None`, erasing all its state. [1](#0-0)  This mirrors the reported bug class: deleting a record (`nftInfo` in the original report, the `Account` here) wipes out a balance field (`unpaidRewards` there, `locked` here) that was never settled/paid out first.

### Finding Description
`action_delete_account` performs several checks before deletion — storage-usage cap (`DeleteAccountWithLargeState`) and gas-key balance sum (`GasKeyBalanceTooHigh`) — but does not read or validate `account.locked()` anywhere in the function body: [2](#0-1) 

The only balance forwarded to the `beneficiary_id` is `account_ref.amount()`: [3](#0-2) 

The account record is then fully removed and set to `None`: [4](#0-3) 

The documented protocol behavior explicitly states that deletion should be rejected with `DeleteAccountStaking` when the account "still has locked balance due to staking": [5](#0-4)  The `DeleteAccountStaking` error variant still exists in the errors enum [6](#0-5) , but it is not raised anywhere inside the current `action_delete_account` implementation shown above — no comparison of `account.locked()` against zero exists in that function. This indicates the locked-balance guard described in the spec is absent from the executed code path.

Separately, the staking invariant docs confirm `amount + locked` is the account's full token balance and that `locked` can only be reduced through the epoch-boundary unlocking process, not through arbitrary user action: [7](#0-6)  If `DeleteAccountAction` can execute while `locked() > 0`, that locked balance is neither transferred to the beneficiary nor accounted for in `tokens_burnt`; it simply vanishes from `ApplyResult`/state along with the deleted account record.

### Impact Explanation
If reachable, this permanently destroys a user's staked/locked tokens with no path to recovery — the funds are not refunded to the beneficiary, not burned via the tracked `tokens_burnt` accounting, and not recoverable since the account and its `locked` field cease to exist. This is a direct, unauthorized and irreversible loss of user funds (frozen/destroyed funds), reachable by any transaction signer who submits a `DeleteAccount` action against their own account while it still carries a locked/staked balance (e.g., during the interval between an unstake proposal and its return at the next epoch's stake-return computation, as described in `update_validator_accounts`) [8](#0-7) .

### Likelihood Explanation
Reaching this requires only a single signed transaction with a `DeleteAccount` action from an account holder who has a non-zero `locked` balance (e.g., mid-unbonding period after issuing an unstake or reduce-stake proposal but before the multi-epoch return completes). No special privileges, validator role, or network position are needed — any regular staking account owner can trigger it.

### Recommendation
Restore/add an explicit check in `action_delete_account` that rejects deletion with `ActionErrorKind::DeleteAccountStaking { account_id }` whenever `account_ref.locked()` is non-zero, matching the documented behavior, before proceeding to remove the account and pay out `amount()` to the beneficiary.

### Proof of Concept
1. Account `alice.near` calls `Stake` to lock `X` tokens, then submits an unstake (`Stake { stake: 0 }`) proposal.
2. Per the staking invariant, `locked` only returns to `amount` after 3 epochs at the epoch boundary via `update_validator_accounts` [9](#0-8) ; during this window `account.locked() > 0`.
3. Before the stake is returned, `alice.near` submits `DeleteAccount { beneficiary_id: bob.near }`.
4. `action_delete_account` executes: it checks storage usage and gas-key balances only, computes payout strictly from `account_ref.amount()`, and deletes the account [1](#0-0) .
5. Result: the `locked` balance `X` is neither transferred to `bob.near` nor recorded in `tokens_burnt`; it is permanently lost from total supply and unrecoverable, matching the "permanently frozen funds" / unauthorized value-loss impact category.

### Citations

**File:** runtime/runtime/src/actions.rs (L330-406)
```rust
pub(crate) fn action_delete_account(
    state_update: &mut TrieUpdate,
    account: &mut Option<Account>,
    actor_id: &mut AccountId,
    receipt: &Receipt,
    result: &mut ActionResult,
    account_id: &AccountId,
    delete_account: &DeleteAccountAction,
    config: &RuntimeConfig,
    current_protocol_version: ProtocolVersion,
) -> Result<(), StorageError> {
    let account_ref = account.as_ref().unwrap();
    let account_storage_usage = if ProtocolFeature::FixDeleteAccountGlobalContractStorageUsage
        .enabled(current_protocol_version)
    {
        let contract_storage = get_contract_storage_usage(state_update, account_id, account_ref)?;
        account_ref.storage_usage().saturating_sub(contract_storage)
    } else {
        // Legacy behavior: only subtracts local contract code, misses the
        // global contract identifier overhead.
        let account_storage_usage = account_ref.storage_usage();
        let code_len = get_code_len_or_default(
            state_update,
            account_id.clone(),
            account_ref.local_contract_hash().unwrap_or_default(),
        )?;
        debug_assert!(
            code_len == 0 || account_storage_usage > code_len,
            "account storage usage should be larger than code size. storage usage: {}, code size: {}",
            account_storage_usage,
            code_len
        );
        account_storage_usage.saturating_sub(code_len)
    };
    if account_storage_usage > Account::MAX_ACCOUNT_DELETION_STORAGE_USAGE {
        result.result =
            Err(ActionErrorKind::DeleteAccountWithLargeState { account_id: account_id.clone() }
                .into());
        return Ok(());
    }
    let gas_key_balance_to_burn = compute_gas_key_balance_sum(state_update, account_id)?;
    if gas_key_balance_to_burn > GasKeyInfo::MAX_BALANCE_TO_BURN {
        result.result = Err(ActionErrorKind::GasKeyBalanceTooHigh {
            account_id: account_id.clone(),
            public_key: None,
            balance: gas_key_balance_to_burn,
        }
        .into());
        return Ok(());
    }
    // We use current amount as a pay out to beneficiary.
    let account_balance = account_ref.amount();
    if account_balance > Balance::ZERO {
        result
            .new_receipts
            .push(Receipt::new_balance_refund(&delete_account.beneficiary_id, account_balance));
    }
    let remove_result = remove_account(state_update, account_id)?;
    result.tokens_burnt =
        result.tokens_burnt.checked_add(gas_key_balance_to_burn).ok_or_else(|| {
            StorageError::StorageInconsistentState("tokens_burnt overflow".to_string())
        })?;
    if remove_result.gas_key_nonce_count > 0 {
        let compute = storage_removes_compute(
            &config.wasm_config.ext_costs,
            remove_result.gas_key_nonce_count,
            remove_result.gas_key_nonce_total_key_bytes,
            AccessKey::NONCE_VALUE_LEN * remove_result.gas_key_nonce_count,
        );
        result.compute_usage = safe_add_compute(result.compute_usage, compute).map_err(|_| {
            StorageError::StorageInconsistentState("compute_usage overflow".to_string())
        })?;
    }
    *actor_id = receipt.predecessor_id().clone();
    *account = None;
    Ok(())
}
```

**File:** docs/RuntimeSpec/Actions.md (L309-314)
```markdown
- If the account still has locked balance due to staking, the following error will be returned

```rust
/// Account is staking and can not be deleted
DeleteAccountStaking { account_id: AccountId }
```
```

**File:** core/primitives/src/errors.rs (L846-849)
```rust
    /// Account is staking and can not be deleted
    DeleteAccountStaking {
        account_id: AccountId,
    } = 7,
```

**File:** docs/ChainSpec/EpochAndStaking/Staking.md (L1-14)
```markdown
# Staking and slashing

## Stake invariant

`Account` has two fields representing its tokens: `amount` and `locked`. `amount + locked` is the total number of
tokens an account has: locking/unlocking actions involve transferring balance between the two fields, and slashing
is done by subtracting from the `locked` value.

On a stake action the balance gets locked immediately (but the locked balance can only increase), and the stake proposal is 
passed to the epoch manager. Proposals get accumulated during an epoch and get processed all at once when an epoch is finalized.
Unlocking only happens at the start of an epoch.

Account's stake is defined per epoch and is stored in `EpochInfo`'s `validators` and `fishermen` sets. `locked` is always
equal to the maximum of the last three stakes and the highest proposal in the current epoch.
```

**File:** runtime/runtime/src/lib.rs (L1748-1793)
```rust
            if let Some(mut account) = account {
                if let Some(reward) = validator_accounts_update.validator_rewards.get(account_id) {
                    tracing::debug!(target: "runtime", %account_id, %reward, locked = %account.locked(), "account adding reward to stake");
                    let locked = account.locked().checked_add(*reward).ok_or_else(|| {
                        RuntimeError::UnexpectedIntegerOverflow("update_validator_accounts".into())
                    })?;
                    account.set_locked(locked).or_inconsistent_state(account_id)?;
                }

                tracing::debug!(target: "runtime",
                       %account_id, locked = %account.locked(), %max_of_stakes,
                       "account stake and max of stakes"
                );
                if account.locked() < *max_of_stakes {
                    return Err(StorageError::StorageInconsistentState(format!(
                        "FATAL: staking invariant does not hold. \
                         Account stake {} is less than maximum of stakes {} in the past three epochs",
                        account.locked(),
                        max_of_stakes)).into());
                }
                let last_proposal = *validator_accounts_update
                    .last_proposals
                    .get(account_id)
                    .unwrap_or(&Balance::ZERO);
                let return_stake = account
                    .locked()
                    .checked_sub(max(*max_of_stakes, last_proposal))
                    .ok_or_else(|| {
                        RuntimeError::UnexpectedIntegerOverflow(
                            "update_validator_accounts - return stake".into(),
                        )
                    })?;
                tracing::debug!(target: "runtime", %account_id, %return_stake, "account return stake");
                let locked = account.locked().checked_sub(return_stake).ok_or_else(|| {
                    RuntimeError::UnexpectedIntegerOverflow(
                        "update_validator_accounts - set_locked".into(),
                    )
                })?;
                account.set_locked(locked).or_inconsistent_state(account_id)?;
                account.set_amount(account.amount().checked_add(return_stake).ok_or_else(
                    || {
                        RuntimeError::UnexpectedIntegerOverflow(
                            "update_validator_accounts - set_amount".into(),
                        )
                    },
                )?);
```
