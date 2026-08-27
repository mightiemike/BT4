## Analysis

The reported bug class — rewards continuing to accrue against a supply denominator that is zero, making them permanently unclaimable — has a direct analog in agave's SIMD-0123 block-revenue-sharing reward path.

### Title
Vote Account Block-Revenue Rewards Become Permanently Unclaimable When a Validator's Delegated Stake Is Zero - (File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs)

### Summary
`calculate_block_reward` distributes a vote account's `pending_delegator_rewards` to individual stake accounts proportionally to `stake / total_active_stake`. When `total_active_stake` for that validator's vote account is `0` (i.e., all its delegated stake has been deactivated), the function short-circuits and returns `0` for every stake account, meaning none of `pending_delegator_rewards` is redeemed that epoch. Because `pending_delegator_rewards` is never decremented in this case, and the vote-program `withdraw` instruction blocks both full account closure and partial withdrawals below `pending_delegator_rewards + rent_exempt_reserve`, these lamports remain locked in the vote account with no path to distribution or recovery as long as delegated stake stays at zero — mirroring the reported class of "reward accrual with unreachable zero-denominator division" causing permanent fund lock-up.

### Finding Description
`calculate_block_reward` computes each stake account's share of a vote account's `pending_delegator_rewards`: [1](#0-0) 

When `total_active_stake == 0` (the vote account has no currently delegated stake for the reward epoch, e.g. `unwrap_or(0)` when the vote pubkey is absent from `reward_epoch_delegated_stakes.delegated_stakes`), the function returns `0` unconditionally — analogous to the external report's `if (_totalSupply == 0) return rewardPerTokenStored;` branch that skips the time-based accrual entirely.

Critically, `pending_delegator_rewards` is only ever consumed via this per-stake-account proportional split (`redeem_delegation_rewards`/`calculate_stake_rewards_and_commissions` calling `calculate_block_reward`) — there is no separate path that reduces or reclaims it when there are zero delegators: [2](#0-1) 

Meanwhile, the vote-program `withdraw` instruction explicitly reserves `pending_delegator_rewards` and refuses both full-close and partial withdrawals that would dip into it: [3](#0-2) 

So once a validator's entire delegated stake is deactivated/withdrawn (an ordinary, permissionless staker action — no malicious actor needed), any `pending_delegator_rewards` already deposited via `DepositDelegatorRewards` (SIMD-0123) becomes permanently stuck: it cannot be redistributed (no stake exists to redeem it against) and cannot be withdrawn by the vote account's authorized withdrawer (blocked by the reserve check).

### Impact Explanation
This is a fund-lock analog of the reported bug: real lamports (deposited block/MEV revenue) become permanently inaccessible — not lost from capitalization/burned, but effectively frozen in the vote account forever (or until new stake happens to be delegated to that exact validator again, which an ordinary staker has no incentive or knowledge to do to "unlock" someone else's stuck rewards). This does not cause double-spend, inflation, or consensus divergence, but it is a genuine, permanent loss of usable funds for whoever deposited them, matching the acknowledged-but-unfixed nature of the referenced Medium-severity finding.

### Likelihood Explanation
Reaching this state requires only ordinary, permissionless actions: a validator's delegators can deactivate and withdraw all their stake (via the stake program) at any time, and revenue depositors use the permissionless `DepositDelegatorRewards` instruction. No special privilege or malicious coordination is required — a validator can simply end up with zero current delegators while still holding undistributed deposited revenue.

### Recommendation
Add a mechanism to let the vote account's authorized withdrawer reclaim or redirect `pending_delegator_rewards` when a vote account has zero active delegated stake for an extended period, or emit these to a designated fallback (e.g., the commission collector) instead of silently returning `0` in `calculate_block_reward` when `total_active_stake == 0`.

### Proof of Concept
1. Validator `V` has stake accounts delegated with lamports `S`, and revenue is deposited into `V`'s vote account via `DepositDelegatorRewards`, setting `pending_delegator_rewards = R`.
2. All delegators to `V` fully deactivate and withdraw their stake, so at the reward-epoch boundary `reward_epoch_delegated_stakes.delegated_stakes.get(&V) == None` (i.e., `total_active_stake == 0`).
3. Test `test_calculate_block_reward_specific` demonstrates the `0` result path: [4](#0-3)  — `get_block_reward_for_test(0, 0, 0, 0) == 0` even though `pending_delegator_rewards` could be nonzero in the vote account.
4. On the next epoch's `calculate_stake_rewards_and_commissions` pass, no stake accounts exist to redeem block reward against, so `R` remains untouched in the vote account.
5. Any attempt to `withdraw` from the vote account is blocked by the `pending_delegator_rewards` reserve check, permanently locking `R` lamports.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-231)
```rust
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
    if total_active_stake == 0 {
        0
    } else {
        let stake = delegation_effective_stake(
            delegation,
            rewarded_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        // During recalculation, if stake account has already received rewards,
        // it's possible to have `stake > total_active_stake`. If
        // `pending_delegator_rewards` is a huge number, we could potentially
        // overflow a `u64`. We can also have individual rewards look greater
        // than the pending rewards. This is harmless in practice, but we
        // clamp it just to be safe
        (pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
            .try_into()
            .unwrap_or(u64::MAX)
            .min(pending_delegator_rewards)
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L820-833)
```rust
                .filter_map(|((stake_pubkey, stake_account), reward_ref)| {
                    let block_reward = if block_revenue_sharing {
                        calculate_block_reward(
                            rewarded_epoch,
                            stake_account.delegation(),
                            stake_history,
                            cached_vote_accounts.distribution_epoch_vote_accounts,
                            ag_epoch_type,
                            new_warmup_cooldown_rate_epoch,
                            use_fixed_point_stake_math,
                        )
                    } else {
                        0
                    };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4320-4332)
```rust
    #[test]
    fn test_calculate_block_reward_specific() {
        // get nothing
        assert_eq!(get_block_reward_for_test(0, 0, 0, 0), 0);
        // get everything
        assert_eq!(get_block_reward_for_test(1, 1, 1, 0), 1);
        // individual stake higher than block reward, capped
        assert_eq!(get_block_reward_for_test(2, 1, 1, 0), 1);
        // not truncated
        assert_eq!(get_block_reward_for_test(1, 10, 10, 0), 1);
        // truncated
        assert_eq!(get_block_reward_for_test(1, 10, 9, 0), 0);
    }
```

**File:** programs/vote/src/vote_state/mod.rs (L1084-1121)
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
```
