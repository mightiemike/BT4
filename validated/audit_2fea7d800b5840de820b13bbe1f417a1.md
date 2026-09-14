### Title
Validator account deletion after full unstake can turn `update_validator_accounts` into an unrecoverable fatal error, halting chunk application - (File: runtime/runtime/src/lib.rs)

### Summary
`Runtime::update_validator_accounts` iterates over every account listed in `ValidatorAccountsUpdate::stake_info` to return stake and credit rewards at an epoch boundary [1](#0-0) . Just like the OpenQ bug where a single failing token transfer aborts the whole claim loop, this loop treats a single missing account as a fatal, unrecoverable error rather than skipping it, aborting the entire chunk's `apply` call.

### Finding Description
For each entry in `stake_info` (accounts that were validators or held stake in the last 3 epochs), the code looks up the account and, if it does not exist at all, checks whether it was owed anything: [2](#0-1) 

If `max_of_stakes > Balance::ZERO`, the function returns `Err(StorageError::StorageInconsistentState(...))` — a fatal, unrecoverable runtime error, not a per-account skip. Contrast this with the *uninitialized* (recreated) account branch a few lines above, where the authors explicitly acknowledged and handled the "validator deletes their account and re-creates it" scenario by skipping it when nothing is owed [3](#0-2) . No equivalent graceful handling exists for the case where the account is fully deleted (not recreated) — the loop can only either succeed for that account or fatally abort the whole batch.

A test explicitly documents that this exact class of failure is treated as a hard `RuntimeError` for the uninitialized-account case when something is owed, proving the failure mode is real and reachable via `Runtime::apply`: [4](#0-3) 

The trigger sequence available to any ordinary validator account (no privileged or malicious-node capability required):
1. Account stakes and becomes an active validator (ordinary `Stake` action).
2. Account calls `Stake` with amount `0` to fully unstake.
3. Once the return-stake epoch boundary is processed while the account still exists, `update_validator_accounts` moves `locked` back to `0` for that account via the `Some(account)` branch [5](#0-4) , so `locked() == 0`.
4. With `locked() == 0`, the account is now free to submit a `DeleteAccount` action (accounts with non-zero locked balance are normally blocked from deletion by the runtime's actor-permission/action checks; once fully unstaked this restriction no longer applies).
5. Because `stake_info` is populated from the "maximum stake across the last 3 epochs" (per the function doc comment) [6](#0-5) , this account can still legitimately appear in `stake_info` with `max_of_stakes > 0` for up to 2 more epoch boundaries even after the account itself has been deleted.
6. At the next such epoch boundary, `update_validator_accounts` looks up the now-nonexistent account, hits the `else if *max_of_stakes > Balance::ZERO` branch, and returns `StorageError::StorageInconsistentState`, aborting the whole `apply` call for that shard/chunk.

I was not able to fully verify, in this session, the exact code in `chain/epoch-manager/src/lib.rs` that populates `stake_info` and the precise length/shape of the "last 3 epochs" retention window, nor the exact `ActionErrorKind` check that blocks `DeleteAccount` while `locked() > 0`; these should be confirmed by a follow-up review of `chain/epoch-manager/src/lib.rs` (stake_info construction) and `runtime/runtime/src/actions.rs` (`action_delete_account` / actor-permission checks).

### Impact Explanation
`StorageError::StorageInconsistentState` returned from `Runtime::apply` is treated as an unrecoverable, "should never happen" condition throughout the runtime (it is explicitly labeled `FATAL` in the surrounding code comments, e.g. [7](#0-6) ). Such errors typically propagate up and panic the node applying the chunk, rather than being handled as an ordinary per-transaction failure. If every honest node hits the same inconsistent state deterministically (since it derives from on-chain data, not from network-specific state), this can produce a transaction-triggered halt of chunk production/application for the affected shard — matching the "transaction-triggered halt" impact category.

### Likelihood Explanation
The trigger requires only ordinary account actions (`Stake` to zero, then `DeleteAccount`) available to any staking account — no validator collusion, no malicious peer, and no special privilege. The multi-epoch delay window (stake return + `stake_info`'s 3-epoch retention) makes exploitation deterministic and reproducible, requiring only patience across a few epochs. Because it depends on this timing window and on confirming the exact `DeleteAccount` guard and `stake_info` population logic (both unverified in this session), likelihood is assessed as moderate rather than certain.

### Recommendation
In `update_validator_accounts`, when an account referenced by `stake_info` (or `validator_rewards`/`last_proposals`) does not exist, do not return a fatal `StorageInconsistentState` error. Instead, mirror the handling already present for the uninitialized-account case: verify that nothing is actually owed (reward, last proposal, and max_of_stakes all effectively unrecoverable because the account is gone) and, if so, safely skip that entry (potentially burning/redirecting the un-deliverable reward instead of aborting the whole chunk), only escalating to a hard failure via metrics/logging rather than an unwind of the entire `apply` call. See `runtime/runtime/src/lib.rs:1717-1838` (`update_validator_accounts`).

### Proof of Concept
Conceptual PoC (requires confirming `stake_info` retention/DeleteAccount guard details in `chain/epoch-manager/src/lib.rs` and `runtime/runtime/src/actions.rs`):
1. Account `V` submits `Stake` action to become an active validator.
2. `V` submits `Stake(0)` to fully unstake.
3. After the epoch boundary where the stake is returned (so `V.locked() == 0`), `V` submits `DeleteAccount`, succeeding since no locked balance blocks deletion.
4. `V`'s account_id remains present in `stake_info` in a subsequent epoch's `ValidatorAccountsUpdate` (per the "max stake over last 3 epochs" rule).
5. At that epoch boundary, `Runtime::update_validator_accounts` looks up `V`, finds no account, sees `max_of_stakes > 0`, and returns `StorageError::StorageInconsistentState`, aborting `apply` for that chunk — reproducible deterministically by any node applying the same chunk, i.e. a transaction-triggered halt.

### Citations

**File:** runtime/runtime/src/lib.rs (L1714-1716)
```rust
    /// Iterates over the validators in the current shard and updates their accounts to return stake
    /// and allocate rewards. Also updates protocol treasury account if it belongs to the current
    /// shard.
```

**File:** runtime/runtime/src/lib.rs (L1717-1722)
```rust
    fn update_validator_accounts(
        &self,
        state_update: &mut TrieUpdate,
        validator_accounts_update: &ValidatorAccountsUpdate,
    ) -> Result<(), RuntimeError> {
        for (account_id, max_of_stakes) in &validator_accounts_update.stake_info {
```

**File:** runtime/runtime/src/lib.rs (L1724-1747)
```rust
            // An uninitialized account has no `locked` field, so none of this applies
            // and it is skipped. The only way it could appear in stake_info is when a
            // validator deletes their account and re-creates in an uninitialized state.
            // None of the checked values could be positive in such case. The check is
            // defense in depth, so that minted tokens do not leak from the supply if
            // this path ever becomes reachable.
            if account.as_ref().is_some_and(|account| !account.is_initialized()) {
                let rewards = &validator_accounts_update.validator_rewards;
                let reward = *rewards.get(account_id).unwrap_or(&Balance::ZERO);
                let proposals = &validator_accounts_update.last_proposals;
                let last_proposal = *proposals.get(account_id).unwrap_or(&Balance::ZERO);
                if *max_of_stakes > Balance::ZERO
                    || reward > Balance::ZERO
                    || last_proposal > Balance::ZERO
                {
                    return Err(StorageError::StorageInconsistentState(format!(
                        "FATAL: staking invariant does not hold. Uninitialized account \
                         {account_id} can hold no locked balance: max of stakes \
                         {max_of_stakes}, reward {reward}, last proposal {last_proposal}"
                    ))
                    .into());
                }
                continue;
            }
```

**File:** runtime/runtime/src/lib.rs (L1748-1795)
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

                set_account(state_update, account_id.clone(), &account);
```

**File:** runtime/runtime/src/lib.rs (L1796-1804)
```rust
            } else if *max_of_stakes > Balance::ZERO {
                // if max_of_stakes > 0, it means that the account must have locked balance
                // and therefore must exist
                return Err(StorageError::StorageInconsistentState(format!(
                    "Account {} with max of stakes {} is not found",
                    account_id, max_of_stakes
                ))
                .into());
            }
```

**File:** runtime/runtime/src/tests/apply.rs (L336-351)
```rust
    // Each of the three needs locked balance the account cannot have.
    let owed = Balance::from_near(1);
    let cases = [
        ("a max of stakes", apply_with(owed, None, None)),
        ("a reward", apply_with(Balance::ZERO, Some(owed), None)),
        ("a last proposal", apply_with(Balance::ZERO, None, Some(owed))),
    ];
    for (what, result) in cases {
        let err = result
            .err()
            .unwrap_or_else(|| panic!("{what} owed to an uninitialized account must fail"));
        assert!(
            matches!(err, RuntimeError::StorageError(StorageError::StorageInconsistentState(_))),
            "unexpected error for {what}: {err:?}"
        );
    }
```
