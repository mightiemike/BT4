## Title
Block reward distribution credits stake accounts with `pending_delegator_rewards` without decrementing the vote account, and `capitalization` is never increased for `block_reward_lamports_distributed` - ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs])

### Summary
`store_stake_accounts_in_partition` credits each stake account with both an inflation `stake_reward` and a `block_reward` drawn from the paying vote account's `pending_delegator_rewards` field [1](#0-0) , but the caller `distribute_epoch_rewards_in_partition` only adjusts `self.capitalization` for `stake_reward_lamports_minted` and `block_reward_lamports_burned`; the successfully-distributed `block_reward_lamports_distributed` is never added to (or accounted against) `capitalization` [2](#0-1) . This mirrors the reported bug class exactly: an accounting counter (`fundingFees`/`capitalization`) is updated using a value that is disconnected from the actual balance change performed on-chain (`distributeYield`/`checked_add_lamports`), with no invariant check tying the two together.

### Finding Description
`build_updated_stake_reward` unconditionally adds both `stake_reward` and `block_reward` lamports to the stake account being stored: [1](#0-0) 

`store_stake_accounts_in_partition` accumulates `block_reward_lamports_distributed` for every successfully-updated stake reward and returns it in `DistributionResults`: [3](#0-2) 

`distribute_epoch_rewards_in_partition` then applies only two capitalization deltas — `fetch_add(stake_reward_lamports_minted)` and `fetch_sub(block_reward_lamports_burned)`. `block_reward_lamports_distributed`, the amount actually credited to stake accounts from `pending_delegator_rewards`, is passed only into `update_epoch_rewards_sysvar` and metrics, never into the `capitalization` counter: [4](#0-3) 

For this to be capitalization-neutral by design, the lamports credited as `block_reward` must already be present in another live account's balance that is decremented by the same amount within this code path — analogous to the vault fee-invariant the audit called out (`lpFee` must be backed by, and no greater than, `fundingFee`). The vote account's `pending_delegator_rewards` field is only a *bookkeeping* counter (tracking how much of the vote account's current lamport balance is earmarked/owed to delegators — see `vote_state::withdraw`, which treats `pending_delegator_rewards` purely as a withdrawal floor and never actually removes it) [5](#0-4) . Nothing in `store_stake_accounts_in_partition` or `build_updated_stake_reward` decrements the vote account's lamport balance or its `pending_delegator_rewards` field when a block reward is paid out to a stake account; only the stake account's lamports increase.

If the vote account's balance is not concurrently reduced elsewhere in the same code path when the block reward is distributed to stakers, the true sum of all account lamports increases by `block_reward_lamports_distributed`, while `Bank::capitalization` (the field used for supply invariant checks and hash calculation) does not track that increase. This produces exactly the "unclear specification/invariant" defect from the analog report: the code computes and moves a fee-like value (`block_reward`) without an on-chain check tying it to the source-of-truth counter that governs total supply.

### Impact Explanation
If the vote-account debit is indeed missing (as the code inspected shows no corresponding decrement), `Bank::capitalization()` silently diverges from the true sum of all account lamports every epoch that block-revenue-sharing rewards are distributed. Because `capitalization` is used to detect corruption (`calculate_capitalization_for_tests`, `MismatchedCapitalization` checks in snapshot loading at `runtime/src/snapshot_bank_utils.rs:224-233` and `:423-432`), and epoch-reward inflation is itself computed as a function of capitalization (`calculate_epoch_inflation_rewards(capitalization, ...)`), any unaccounted growth compounds every epoch, distorting the entire cluster's inflation schedule and the results of `verify_accounts`/accounts-lt-hash based crash-consistency checks.

### Likelihood Explanation
This path is only exercised when the `block_revenue_sharing` feature (SIMD-0123) is active and any validator has a non-zero `pending_delegator_rewards`, i.e., every honest node that reaches this feature-gated code during epoch-boundary reward distribution — not a validator/operator-privileged action, but a deterministic consensus-critical accounting path executed by every node.

### Recommendation
Confirm (via a full trace of `vote_state::deposit_delegator_rewards`/withdraw and `distribute_epoch_rewards_in_partition`) whether the vote account's lamports/`pending_delegator_rewards` are decremented elsewhere for the exact `block_reward_lamports_distributed` amount before this reward is credited to stake accounts. If not, add an explicit debit of the vote account (and `pending_delegator_rewards`) for the distributed block reward within `store_stake_accounts_in_partition`/`build_updated_stake_reward`, and add an assertion/invariant in `distribute_epoch_rewards_in_partition` that `capitalization` delta equals the net lamport delta actually applied to accounts (stake reward mint + block reward move - burns), matching the report's recommendation to encode the `lpFee ≤ fundingFee`-style invariant on-chain rather than assuming caller-supplied inputs are correct.

### Proof of Concept
Not independently reproducible from static analysis alone: this requires tracing every mutation site of `pending_delegator_rewards` and vote-account lamports across the reward-calculation and distribution code paths to confirm the debit is truly absent (the search performed did not locate any lamport decrement on the vote account tied to `block_reward` distribution, but this is not a substitute for compiling/running `test_distribute_with_increased_rent`-style tests instrumented to assert `sum(all account lamports) == capitalization` after a `block_revenue_sharing`-enabled distribution). A concrete PoC would extend `runtime/src/bank/partitioned_epoch_rewards/distribution.rs::test_distribute_with_increased_rent` to set nonzero `pending_delegator_rewards` on the paying vote account, run `distribute_epoch_rewards_in_partition`, and assert `bank.capitalization() == bank.calculate_capitalization_for_tests()` (currently only `stake_reward` is asserted at line 1211, `block_reward` is not included in the capitalization-delta assertion).

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L192-204)
```rust
        // increase total capitalization by the distributed rewards
        self.capitalization
            .fetch_add(stake_reward_lamports_minted, Relaxed);

        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);

        // decrease distributed capital from epoch rewards sysvar
        self.update_epoch_rewards_sysvar(
            stake_reward_lamports_minted + stake_reward_lamports_burned,
            block_reward_lamports_distributed + block_reward_lamports_burned,
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-267)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L380-422)
```rust
            let stake_pubkey = partitioned_stake_reward.stake_pubkey;
            let stake_reward_amount = partitioned_stake_reward.inflation.stake_reward;
            let block_reward_amount = partitioned_stake_reward.block_reward;

            match Self::build_updated_stake_reward(
                self.epoch,
                stake_history,
                new_warmup_cooldown_rate_epoch,
                stakes_cache_accounts,
                partitioned_stake_reward,
                rent,
                adjust_delegations_for_rent,
                use_fixed_point_stake_math,
            ) {
                Ok(stake_reward) => {
                    stake_reward_lamports_minted += stake_reward_amount;
                    block_reward_lamports_distributed += block_reward_amount;
                    updated_stake_rewards.push(stake_reward);
                }
                Err(err) => {
                    error!(
                        "bank::distribution::store_stake_accounts_in_partition() failed for \
                         {stake_pubkey}, {stake_reward_amount} lamports burned: {err:?}"
                    );
                    stake_reward_lamports_burned += stake_reward_amount;
                    block_reward_lamports_burned += block_reward_amount;
                }
            }
        }
        drop(stakes_cache);
        self.store_accounts(
            (self.slot(), &updated_stake_rewards[..]),
            // Reuse the rewards calculation thread pool to parallelize
            // loading the previous versions of the stake accounts.
            Some(crate::bank::rewards_calculation_thread_pool()),
        );
        DistributionResults {
            stake_reward_lamports_minted,
            stake_reward_lamports_burned,
            block_reward_lamports_distributed,
            block_reward_lamports_burned,
            updated_stake_rewards,
        }
```

**File:** programs/vote/src/vote_state/mod.rs (L1084-1122)
```rust
    // Always zero until SIMD-0123 is activated.
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();

    if remaining_balance == 0 {
        // SIMD-0123: vote account cannot be closed if
        // pending_delegator_rewards > 0.
        if pending_delegator_rewards > 0 {
            return Err(InstructionError::InsufficientFunds);
        }

        let reject_active_vote_account_close = vote_state
            .epoch_credits()
            .last()
            .map(|(last_epoch_with_credits, _, _)| {
                let current_epoch = clock.epoch;
                // if current_epoch - last_epoch_with_credits < 2 then the validator has received credits
                // either in the current epoch or the previous epoch. If it's >= 2 then it has been at least
                // one full epoch since the validator has received credits.
                current_epoch.saturating_sub(*last_epoch_with_credits) < 2
            })
            .unwrap_or(false);

        if reject_active_vote_account_close {
            return Err(VoteError::ActiveVoteAccountClose.into());
        } else {
            // Deinitialize upon zero-balance
            VoteStateHandler::deinitialize_vote_account_state(&mut vote_account, target_version)?;
        }
    } else {
        // SIMD-0123: withdrawable balance when pending_delegator_rewards > 0
        // is lamports - pending_delegator_rewards - rent_exempt_minimum.
        let min_rent_exempt_balance = rent_sysvar.minimum_balance(vote_account.get_data().len());
        let min_balance = min_rent_exempt_balance
            .checked_add(pending_delegator_rewards)
            .ok_or(InstructionError::ArithmeticOverflow)?;
        if remaining_balance < min_balance {
            return Err(InstructionError::InsufficientFunds);
        }
    }
```
