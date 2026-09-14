## Analysis

The Sherlock finding is a "loop breaks entirely because one recipient can't receive funds" bug class: `_restoreLiquidity` iterates over creditors and does a hard token transfer to each; if a single recipient is blacklisted, the whole repayment loop reverts, freezing funds for every other participant in that call.

The closest reachable analog in nearcore is `Runtime::update_validator_accounts` in [1](#0-0) , which is invoked unconditionally on the first block of every epoch to pay out stake returns/rewards, and specifically the protocol-treasury-account branch: [2](#0-1) 

If `protocol_treasury_account_id` is set for the shard (which happens every epoch boundary) and the account is not present in state, `get_account` returns `None` and the code turns this into `StorageError::StorageInconsistentState("Protocol treasury account {} is not found")`, which is treated as a **fatal** error and propagates as `RuntimeError` out of `apply`, i.e. it fails the whole chunk application, not just the recipient's own state change.

The `DeleteAccount` action only blocks deletion when the account holds locked (staked) balance — `action_delete_account` in `runtime/runtime/src/actions.rs:343` enforces "requires zero locked stake" via `check_actor_permissions`, but does not special-case the configured `protocol_treasury_account`. Since the treasury account normally never stakes (it only accumulates `amount`, never `locked`), its owner can submit an ordinary `DeleteAccount` transaction at any time and pass that check.

Because `update_validator_accounts` runs this treasury lookup unconditionally at *every* subsequent epoch boundary on the shard that owns the treasury account, once the account is deleted, every future first-of-epoch chunk apply on that shard hits the `ok_or_else` fatal branch and fails — permanently, until someone manually restores the account in state (which requires operator intervention, not a normal transaction). This mirrors the report's pattern exactly: a single account being “unreachable” (blacklisted / deleted) breaks a fund-distribution loop for an entire batch of participants (here, every validator receiving stake return / reward on that shard), and the failure is not isolated per-recipient the way ordinary receipt failures are (contrast with the per-receipt isolation explicitly tested in `runtime/runtime/src/tests/apply.rs:5199-5269`, which shows receipts are normally isolated — this treasury path is a case where they are not).

By contrast, the ordinary validator-reward branch of the same loop is defended: an uninitialized/missing validator account is explicitly special-cased and skipped when nothing is owed (`runtime/runtime/src/lib.rs:1730-1747`, exercised by `test_apply_validator_update_uninitialized_account` in `runtime/runtime/src/tests/apply.rs:263-352`), and a validator cannot delete their account while it still holds locked stake. The protocol-treasury branch has no equivalent defense.

### Title
Deleting the protocol treasury account permanently halts chunk application on its shard - (File: runtime/runtime/src/lib.rs)

### Summary
`Runtime::update_validator_accounts` distributes epoch-boundary stake returns and rewards in a single all-or-nothing pass. For the protocol treasury account specifically, it does a plain `get_account(...).ok_or_else(FATAL)` lookup with no existence/defensive handling. Any transaction that deletes the treasury account (a normal, permitted `DeleteAccount` action, since the treasury never holds locked stake) causes this lookup to fail on the very next epoch boundary and forever after, turning chunk application into a hard failure.

### Finding Description
`update_validator_accounts` is called from `Runtime::apply` at the first block of every epoch for the shard hosting the affected accounts (`runtime/runtime/src/lib.rs`, called around line 1752 per `protocol-model/spec/runtime-execution.md:30`). For the treasury account it does:
```
let mut account = get_account(state_update, account_id)?.ok_or_else(|| {
    StorageError::StorageInconsistentState(format!(
        "Protocol treasury account {} is not found", account_id
    ))
})?;
```
`StorageInconsistentState` is treated as a FATAL, unrecoverable error throughout the runtime (the same class used for the validator staking-invariant assertions), and it is returned out of `apply`, failing the entire chunk application — not merely the treasury's own state transition.

The treasury account is an ordinary account as far as `DeleteAccount` is concerned: `action_delete_account` (`runtime/runtime/src/actions.rs:343`) only requires zero locked stake, which the treasury account trivially satisfies (it never stakes). So its owner can submit a single, valid `DeleteAccount` transaction and remove the account from state. Every subsequent epoch boundary, `update_validator_accounts` will run again on that shard, fail to find the account, and abort chunk application with a fatal error, permanently.

### Impact Explanation
This is a transaction-triggered halt: once the treasury account is deleted, the shard that hosts it can never successfully apply the first chunk of any future epoch, since the fatal error recurs deterministically every epoch. This blocks reward/stake-return distribution and effectively halts block/chunk production progress for that shard indefinitely, requiring manual state surgery (not a normal transaction) to recover. This satisfies the "transaction-triggered halt" acceptance criterion.

### Likelihood Explanation
Reachable with a single ordinary transaction (`DeleteAccount`) sent by whoever controls the private key of the account configured as `protocol_treasury_account` in genesis — no special validator, node, or network privilege is required beyond owning that account's key, and the check that would normally block deletion (locked stake) does not apply to the treasury account. No other invariant in the codebase currently prevents deleting this specific account id.

### Recommendation
Do not treat a missing protocol treasury account as a fatal, chunk-halting error. Either (a) skip crediting the reward gracefully (matching the pattern already used for uninitialized validator accounts at `runtime/runtime/src/lib.rs:1730-1747`), or (b) forbid deletion of the account whose id equals `protocol_treasury_account_id` in `action_delete_account`/`check_actor_permissions`, so it can never be removed from state while still referenced by the runtime.

### Proof of Concept
1. Genesis configures `protocol_treasury_account = "near"` (or any configured treasury id) with zero locked stake.
2. The controller of `"near"` submits a valid `DeleteAccount` action (allowed because locked stake is zero), refunding balance to a beneficiary; the account is removed from state.
3. At the next epoch boundary, the shard owning `"near"` builds a `ValidatorAccountsUpdate` with `protocol_treasury_account_id = Some("near")`.
4. `update_validator_accounts` calls `get_account(state_update, "near")`, gets `None`, and returns `StorageError::StorageInconsistentState("Protocol treasury account near is not found")`.
5. `Runtime::apply` propagates this as a `RuntimeError`, failing chunk application for that shard; this repeats at every subsequent epoch boundary since the account is never re-created by protocol logic.

### Citations

**File:** runtime/runtime/src/lib.rs (L1710-1721)
```rust

        Ok(None)
    }

    /// Iterates over the validators in the current shard and updates their accounts to return stake
    /// and allocate rewards. Also updates protocol treasury account if it belongs to the current
    /// shard.
    fn update_validator_accounts(
        &self,
        state_update: &mut TrieUpdate,
        validator_accounts_update: &ValidatorAccountsUpdate,
    ) -> Result<(), RuntimeError> {
```

**File:** runtime/runtime/src/lib.rs (L1807-1835)
```rust
        if let Some(account_id) = &validator_accounts_update.protocol_treasury_account_id {
            // If protocol treasury stakes, then the rewards was already distributed above.
            if !validator_accounts_update.stake_info.contains_key(account_id) {
                let mut account = get_account(state_update, account_id)?.ok_or_else(|| {
                    StorageError::StorageInconsistentState(format!(
                        "Protocol treasury account {} is not found",
                        account_id
                    ))
                })?;
                let treasury_reward = *validator_accounts_update
                    .validator_rewards
                    .get(account_id)
                    .ok_or_else(|| {
                        StorageError::StorageInconsistentState(format!(
                            "Validator reward for the protocol treasury account {} is not found",
                            account_id
                        ))
                    })?;
                account.set_amount(account.amount().checked_add(treasury_reward).ok_or_else(
                    || {
                        RuntimeError::UnexpectedIntegerOverflow(
                            "update_validator_accounts - treasure_reward".into(),
                        )
                    },
                )?);
                set_account(state_update, account_id.clone(), &account);
            }
        }
        state_update.commit(StateChangeCause::ValidatorAccountsUpdate);
```
