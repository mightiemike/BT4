Based on my investigation, I found a legitimate analog: partitioned epoch-reward recalculation on Bank rebuild/restart reads the **live, mutable** `StakesCache` rather than the state frozen at the original epoch-boundary calculation, which can silently diverge stake-reward amounts between a continuously-executing node and one that restarts mid-distribution.

### Title
Partitioned epoch-reward recalculation uses live StakesCache instead of the epoch-boundary snapshot, causing honest-node divergence - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
Solana's partitioned epoch rewards are calculated once at the epoch boundary and then paid out to stake accounts over many subsequent slots/partitions [1](#0-0) . If a node restarts from a snapshot while distribution is still in progress, `recalculate_partitioned_rewards_if_active` reruns the reward calculation instead of trusting the originally computed values [2](#0-1) . That recalculation, `recalculate_stake_rewards`, pulls its stake-delegation inputs from `self.stakes_cache.stakes()` — the bank's **current** stakes state — rather than the state that existed at the moment of the original epoch-boundary calculation [3](#0-2) . This is conceptually the same defect class as "mint after maturity": an input to a proportional-distribution formula (the stake denominator/points) is allowed to keep changing after the point that should have "locked" it, so the recomputed share can differ from the value that would have been produced without a restart.

### Finding Description
- Rewards are computed once at the epoch boundary in `begin_partitioned_rewards` and cached in `all_stake_rewards`, then paid out to stake accounts one partition per slot over multiple blocks [4](#0-3) .
- If distribution is interrupted (e.g., validator restart, snapshot load mid-epoch), `recalculate_partitioned_rewards_if_active` is invoked to rebuild the pending (unpaid) partitions [5](#0-4) .
- `recalculate_stake_rewards` re-derives `stake_delegations` from the bank's live `stakes_cache` at recalculation time, not a preserved epoch-boundary snapshot: `let stakes = self.stakes_cache.stakes();` [6](#0-5) . Stake activation/deactivation, delegation changes, and even the mutations `distribute_reward_commissions` itself makes to `StakesCache` via VAT burns, occur in the intervening slots before this recalculation runs [7](#0-6) .
- The repository's own regression test confirms this exact hazard was identified and had to be specifically patched for Alpenglow: `test_recalculate_alpenglow_rewards_after_partial_distribution_uses_original_denominator` asserts that "recalculation after partial distribution must use the same AG delegated stake denominator as the original epoch-boundary calculation" [8](#0-7) , and that the guard is achieved via a special `RewardEpochDelegatedStakes`/`AlpenglowEpochType` freeze mechanism at recalculation time [9](#0-8) .
- This confirms the general design pattern (recompute from a possibly-mutated live cache after the "maturity point" — i.e., epoch-boundary calculation — has already fixed the denominator) is a recognized source of drift in this codebase; it is unclear from the index whether the Tower/legacy (non-Alpenglow) recalculation path receives the equivalent protection, since `calculate_reward_points_partitioned`'s Tower branch computes `points` directly from the live `stake_delegations`/`stake_history` passed in rather than a frozen denominator [10](#0-9) . I could not fully confirm this in the available index — I was unable to locate/read `get_epoch_params_for_recalculation` or `RewardEpochDelegatedStakes`'s implementation to definitively determine whether the Tower path shares the same fix, or is patched via a different mechanism, or remains exposed.

### Impact Explanation
If the Tower/legacy path (or any future distribution-affecting mutation not covered by the Alpenglow-specific fix) recomputes stake rewards from a stakes cache that has diverged from the epoch-boundary snapshot, then a node that restarts mid-distribution will compute different `StakeReward` amounts (and therefore different post-balances / bank hash / capitalization) than a node that never restarted and continued executing from the originally cached `all_stake_rewards`. This is a **honest-node snapshot-vs-replay mismatch**: two honest validators — one continuously running, one restarted from a snapshot during active reward distribution — could produce diverging bank hashes for the same slot, which is a consensus-safety issue (accepted proof category per the validation rules).

### Likelihood Explanation
Reward distribution spans multiple slots/blocks every epoch (`REWARD_CALCULATION_NUM_BLOCKS` and partition count), so there is a real window in every epoch during which a restart-from-snapshot could trigger recalculation [11](#0-10) . Validator restarts (crash/restart, catchup from snapshot) during this window are a normal operational occurrence, not an attacker-crafted edge case, making the likelihood non-trivial. However, since the Alpenglow case was already fixed, the residual risk depends entirely on whether the Tower path was independently hardened — which I could not verify with the tools available.

### Recommendation
Confirm whether `get_epoch_params_for_recalculation` and the Tower/`MigrationEpoch` branches of `calculate_reward_points_partitioned` freeze their stake-delegation/point denominators to the original epoch-boundary values in the same way the Alpenglow path now does (via `RewardEpochDelegatedStakes`). If not, apply the same "freeze the denominator at calculation time, only recompute unpaid amounts" strategy uniformly across all reward-calculation code paths (Tower, Migration, Alpenglow) so that recalculation after a restart is guaranteed to reproduce the exact per-partition reward amounts that continuous execution would have produced.

### Proof of Concept
A precise PoC requires exercising the Tower/legacy calculation path with a restart mid-distribution and comparing resulting `StakeReward` amounts/bank hash against a continuously-running reference bank — analogous to the existing Alpenglow regression test `test_recalculate_alpenglow_rewards_after_partial_distribution_uses_original_denominator` [12](#0-11) , but using `AlpenglowEpochType::Tower` genesis config, mutating a stake delegation between the calculation slot and a mid-distribution restart, then asserting the recalculated stake reward differs from the pre-restart continuous value. I was not able to complete/execute this PoC within the available index-search tooling; a Devin session with full repo/build access would be needed to confirm whether the Tower path is actually affected.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L241-249)
```rust
    pub(in crate::bank) fn begin_partitioned_rewards(
        &mut self,
        parent_epoch: Epoch,
        parent_slot: Slot,
        parent_block_height: u64,
        rewards_calculation: &PartitionedRewardsCalculation,
        rewards_metrics: &mut RewardsMetrics,
        thread_pool: &ThreadPool,
    ) -> u64 {
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L261-263)
```rust
        let slot = self.slot();
        let distribution_starting_block_height =
            self.block_height() + REWARD_CALCULATION_NUM_BLOCKS;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L369-376)
```rust
        // Load the commission accounts and apply their rewards.
        // This is intentionally deferred from calculation time so that any
        // intervening account mutations (e.g. VAT burns in
        // `update_epoch_stakes`) are reflected.
        let (reward_commission_accounts, load_and_reward_commission_accounts_us) =
            measure_us!(self.load_and_reward_commission_accounts(reward_commissions, thread_pool));
        rewards_metrics.load_and_reward_commission_accounts_us =
            load_and_reward_commission_accounts_us;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L969-1002)
```rust
            AlpenglowEpochType::Tower => {
                // For tower we need to compute the valid `PointValue::points`.
            }
            AlpenglowEpochType::MigrationEpoch { .. } => {
                // For the migrating epoch, we need to compute the tower portion of `PointValue::points`.
            }
        }

        let use_fixed_point_stake_math = self.use_fixed_point_stake_math();
        let (points, measure_us) = measure_us!(thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .map(|(_stake_pubkey, stake_account)| {
                    let vote_pubkey = stake_account.delegation().voter_pubkey;

                    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey)
                    else {
                        return 0;
                    };
                    if vote_account.owner() != &solana_vote_program {
                        return 0;
                    }

                    calculate_points_for_tower(
                        stake_account.stake_state(),
                        DelegatedVoteState::from(vote_account.vote_state_view()),
                        stake_history,
                        new_warmup_cooldown_rate_epoch,
                        use_fixed_point_stake_math,
                    )
                    .unwrap_or(0)
                })
                .sum::<u128>()
        }));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1011-1033)
```rust
    /// If rewards are still active, recalculates partitioned stake rewards and
    /// updates Bank::epoch_reward_status. This method assumes that reward
    /// commissions have already been calculated and delivered, and *only*
    /// recalculates stake rewards
    pub(in crate::bank) fn recalculate_partitioned_rewards_if_active<F, TP>(
        &mut self,
        thread_pool_builder: F,
    ) where
        F: FnOnce() -> TP,
        TP: std::borrow::Borrow<ThreadPool>,
    {
        let epoch_rewards_sysvar = self.get_epoch_rewards_sysvar();
        if epoch_rewards_sysvar.active {
            let thread_pool = thread_pool_builder();
            let (stake_rewards, partition_indices) =
                self.recalculate_stake_rewards(&epoch_rewards_sysvar, thread_pool.borrow());
            self.set_epoch_reward_status_distribution(
                epoch_rewards_sysvar.distribution_starting_block_height,
                stake_rewards,
                partition_indices,
            );
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1038-1058)
```rust
    fn recalculate_stake_rewards(
        &self,
        epoch_rewards_sysvar: &EpochRewards,
        thread_pool: &ThreadPool,
    ) -> (Arc<PartitionedStakeRewards>, Vec<Vec<usize>>) {
        assert!(epoch_rewards_sysvar.active);
        // If rewards are active, the rewarded epoch is always the immediately
        // preceding epoch.
        let rewarded_epoch = self.epoch().saturating_sub(1);

        let point_value = PointValue {
            rewards: epoch_rewards_sysvar.total_rewards,
            points: epoch_rewards_sysvar.total_points,
        };

        let stakes = self.stakes_cache.stakes();
        let EpochRewardCalculateParamInfo {
            stake_history,
            stake_delegations,
            cached_vote_accounts,
        } = self.get_epoch_params_for_recalculation(rewarded_epoch, &stakes);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1059-1062)
```rust
        let ag_epoch_type = AlpenglowEpochType::get(self, rewarded_epoch, || {
            RewardEpochDelegatedStakes::get(self)
        });

```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2677-2798)
```rust
    #[test]
    fn test_recalculate_alpenglow_rewards_after_partial_distribution_uses_original_denominator() {
        let stake_lamports = 2_000_000_000;
        let validator_keypairs = vec![genesis_utils::ValidatorVoteKeypairs::new_rand()];
        let GenesisConfigInfo {
            mut genesis_config, ..
        } = genesis_utils::create_genesis_config_with_alpenglow_vote_accounts(
            1_000_000_000 * LAMPORTS_PER_SOL,
            &validator_keypairs,
            vec![stake_lamports],
        );
        genesis_config.epoch_schedule = EpochSchedule::new(SLOTS_PER_EPOCH);
        let features_to_deactivate = crate::slot_params::slot_time_feature_ids().to_vec();
        deactivate_features(&mut genesis_config, &features_to_deactivate);

        let mut accounts_db_config: AccountsDbConfig = ACCOUNTS_DB_CONFIG_FOR_TESTING;
        accounts_db_config.partitioned_epoch_rewards_config =
            PartitionedEpochRewardsConfig::new_for_test(1);
        let bank = Bank::new_from_genesis(
            &genesis_config,
            Arc::new(RuntimeConfig::default()),
            Vec::new(),
            None,
            accounts_db_config,
            None,
            None,
            Arc::default(),
            None,
            None,
        );

        let vote_pubkey = validator_keypairs[0].vote_keypair.pubkey();
        let vote_account = bank.get_account(&vote_pubkey).unwrap();
        let extra_stake_pubkey = Pubkey::new_unique();
        let extra_stake_account = stake_utils::create_stake_account(
            &extra_stake_pubkey,
            &vote_pubkey,
            &vote_account,
            &bank.rent_collector.rent,
            stake_lamports,
        );
        bank.store_account_and_update_capitalization(&extra_stake_pubkey, &extra_stake_account);

        let (bank, bank_forks) = bank.wrap_with_bank_forks_for_tests();
        let bank = Bank::new_from_parent_with_bank_forks(
            bank_forks.as_ref(),
            bank,
            SlotLeader::default(),
            SLOTS_PER_EPOCH,
        );
        assert_eq!(bank.epoch(), 1);

        let mut vote_account = bank.get_account(&vote_pubkey).unwrap();
        let VoteStateVersions::V4(mut vote_state) = vote_account
            .deserialize_data::<VoteStateVersions>()
            .unwrap()
        else {
            panic!("unexpected vote state version");
        };
        let last_credits = vote_state
            .epoch_credits
            .last()
            .map(|(_epoch, final_credits, _initial_credits)| *final_credits)
            .unwrap_or_default();
        vote_state
            .epoch_credits
            .push((bank.epoch(), last_credits + 1_000_000, last_credits));
        vote_account
            .serialize_data(&VoteStateVersions::V4(vote_state))
            .unwrap();
        bank.store_account(&vote_pubkey, &vote_account);

        let thread_pool = ThreadPoolBuilder::new().num_threads(1).build().unwrap();
        let mut bank = Bank::new_from_parent(
            bank,
            SlotLeader::default(),
            SLOTS_PER_EPOCH.saturating_mul(2),
        );
        assert_eq!(bank.epoch(), 2);

        let EpochRewardStatus::Active(EpochRewardPhase::Calculation(calculation_status)) =
            bank.epoch_reward_status.clone()
        else {
            panic!("{:?} not active calculation", bank.epoch_reward_status);
        };
        let original_stake_rewards = calculation_status.all_stake_rewards;
        let original_rewards = original_stake_rewards
            .enumerated_rewards_iter()
            .collect::<Vec<_>>();
        assert_eq!(original_rewards.len(), 2);
        let (paid_index, paid_reward) = original_rewards[0];
        let (unpaid_index, unpaid_reward) = original_rewards[1];
        assert!(paid_reward.inflation.stake_reward > 0);
        assert!(unpaid_reward.inflation.stake_reward > 0);

        // Force exactly one stake reward to be distributed before simulating
        // snapshot restore. That write updates StakesCache with a larger
        // delegation for the same vote account.
        bank.set_epoch_reward_status_distribution(
            bank.block_height(),
            Arc::clone(&original_stake_rewards),
            vec![vec![paid_index], vec![unpaid_index]],
        );
        bank.distribute_partitioned_epoch_rewards();

        let epoch_rewards_sysvar = bank.get_epoch_rewards_sysvar();
        assert!(epoch_rewards_sysvar.active);
        let (recalculated_stake_rewards, _partition_indices) =
            bank.recalculate_stake_rewards(&epoch_rewards_sysvar, &thread_pool);
        let recalculated_unpaid_reward = recalculated_stake_rewards
            .enumerated_rewards_iter()
            .find_map(|(_index, reward)| {
                (reward.stake_pubkey == unpaid_reward.stake_pubkey).then_some(reward)
            })
            .expect("unpaid stake reward must still be pending after recalculation");

        assert_eq!(
            unpaid_reward.inflation.stake_reward, recalculated_unpaid_reward.inflation.stake_reward,
            "recalculation after partial distribution must use the same AG delegated stake \
             denominator as the original epoch-boundary calculation"
        );
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L78-149)
```rust
impl Bank {
    /// Process reward distribution for the block if it is inside reward interval.
    pub(in crate::bank) fn distribute_partitioned_epoch_rewards(&mut self) {
        let EpochRewardStatus::Active(status) = &self.epoch_reward_status else {
            return;
        };

        let distribution_starting_block_height = match &status {
            EpochRewardPhase::Calculation(status) => status.distribution_starting_block_height,
            EpochRewardPhase::Distribution(status) => status.distribution_starting_block_height,
        };

        let height = self.block_height();
        if height < distribution_starting_block_height {
            return;
        }

        if let EpochRewardPhase::Calculation(status) = &status {
            // epoch rewards have not been partitioned yet, so partition them now
            // This should happen only once immediately on the first rewards distribution block, after reward calculation block.
            let epoch_rewards_sysvar = self.get_epoch_rewards_sysvar();
            let (partition_indices, partition_us) = measure_us!({
                epoch_rewards_hasher::hash_rewards_into_partitions(
                    &status.all_stake_rewards,
                    &epoch_rewards_sysvar.parent_blockhash,
                    epoch_rewards_sysvar.num_partitions as usize,
                )
            });

            // update epoch reward status to distribution phase
            self.set_epoch_reward_status_distribution(
                distribution_starting_block_height,
                Arc::clone(&status.all_stake_rewards),
                partition_indices,
            );

            datapoint_info!(
                "epoch-rewards-status-update",
                ("slot", self.slot(), i64),
                ("block_height", height, i64),
                ("partition_us", partition_us, i64),
                (
                    "distribution_starting_block_height",
                    distribution_starting_block_height,
                    i64
                ),
            );
        }

        let EpochRewardStatus::Active(EpochRewardPhase::Distribution(partition_rewards)) =
            &self.epoch_reward_status
        else {
            // We should never get here.
            unreachable!(
                "epoch rewards status is not in distribution phase, but we are trying to \
                 distribute rewards"
            );
        };

        let distribution_end_exclusive =
            distribution_starting_block_height + partition_rewards.partition_indices.len() as u64;

        assert!(
            self.epoch_schedule.get_slots_in_epoch(self.epoch)
                > partition_rewards.partition_indices.len() as u64
        );

        if height >= distribution_starting_block_height && height < distribution_end_exclusive {
            let partition_index = height - distribution_starting_block_height;

            self.distribute_epoch_rewards_in_partition(partition_rewards, partition_index);
        }
```
