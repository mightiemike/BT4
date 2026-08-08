### Title
Snapshot-restore recalculation of partitioned epoch rewards excludes attacker-closed stake accounts that the live/continuously-running bank would still burn, causing capitalization/bank-hash divergence - ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs] / [runtime/src/bank/partitioned_epoch_rewards/calculation.rs] / [runtime/src/bank.rs])

### Summary
`EpochRewardStatus` (which holds `all_stake_rewards` and `partition_indices`) is explicitly excluded from the serialized snapshot state and is reset to `EpochRewardStatus::default()` on load [1](#0-0) , then rebuilt by `initialize_after_snapshot_restore` → `recalculate_partitioned_rewards_if_active` → `recalculate_stake_rewards`, which recomputes stake rewards from the **current** `stakes_cache` at the snapshot slot rather than replaying the original epoch-boundary computed list [2](#0-1) . Because this recomputation iterates only over currently-delegated stake accounts, a stake account that an unprivileged user deactivated and closed between the epoch boundary and the snapshot slot is silently absent from the recomputed list and its `partition_indices`, whereas the continuously-running (non-restarted) bank still carries that pubkey in its original in-memory `all_stake_rewards` and will attempt to pay/burn it via `build_updated_stake_reward` when its scheduled partition arrives [3](#0-2) .

### Finding Description
The distribution flow works as follows:
- At the epoch boundary, `all_stake_rewards` and (once distribution starts) `partition_indices` are computed once and stored in `Bank::epoch_reward_status` (`EpochRewardPhase::Distribution(StartBlockHeightAndPartitionedRewards{...})`) [4](#0-3) . On every subsequent block until `distribution_end_exclusive`, `distribute_partitioned_epoch_rewards` simply looks up `partition_indices[partition_index]` from this fixed in-memory structure and calls `distribute_epoch_rewards_in_partition` [5](#0-4) .
- `store_stake_accounts_in_partition` resolves each `partitioned_stake_reward.stake_pubkey` against `self.stakes_cache.stakes().stake_delegations()` **as of the current bank**, not as of the epoch boundary [6](#0-5) . If the pubkey is missing (attacker closed the stake account after the epoch boundary but before its scheduled distribution block), `build_updated_stake_reward` returns `DistributionError::AccountNotFound`, and the reward amount is explicitly **burned** (capitalization not incremented for the mint, and `stake_reward_lamports_burned` is tracked, decreasing net capitalization change) [7](#0-6) .
- On snapshot restore, `epoch_reward_status` is reset to default (not deserialized) [8](#0-7) , then `initialize_after_snapshot_restore` is invoked [9](#0-8) , which recalculates the whole `all_stake_rewards`/`partition_indices` set fresh from `recalculate_stake_rewards`. That function pulls `stake_delegations` straight from the **current** `stakes_cache` (i.e., as of the snapshot slot) via `get_epoch_params_for_recalculation`, and `calculate_stake_rewards_and_commissions` only iterates over currently-existing delegations, so a closed account's pubkey is simply never emitted into the recomputed `PartitionedStakeRewards` list at all [10](#0-9) .
- Consequently the two lineages diverge in *what set of stake pubkeys is even considered* for the remaining partitions: the live bank still carries the closed pubkey in its stale `all_stake_rewards` (destined to be burned when its turn comes), while the snapshot-restored bank never has that pubkey in its recomputed list at all. This changes the total count/ordering of remaining rewards fed into `hash_rewards_into_partitions`, altering the resulting `partition_indices` grouping for every remaining stake pubkey (not just the closed one), and changes whether/how the burn accounting for that lamport amount is applied to `stake_reward_lamports_burned` / capitalization / the `EpochRewards` sysvar's `distributed_rewards` bookkeeping. The existing regression test `test_recalculate_partitioned_rewards_after_partial_distribution` only validates that an *unpaid-but-still-delegated* pubkey's reward amount is stable across recalculation using a cached AG denominator [11](#0-10) ; it does not cover the case of the pubkey disappearing from the stakes cache entirely between the epoch boundary and snapshot slot, which is exactly the scenario this recalculation path cannot reproduce identically to the live bank's `AccountNotFound`/burn path.

### Impact Explanation
This produces an honest-node snapshot-vs-replay divergence: a validator that restarts from a snapshot mid-distribution computes a different `partition_indices` mapping (and possibly different total burned/minted lamports, differing `capitalization` and `EpochRewards.distributed_rewards`) than a validator that never restarted and continued the original in-memory distribution schedule. This is a capitalization/bank-hash divergence category — the exact class this question is probing (SNAPSHOT_FIDELITY). It is achievable purely by an unprivileged staker deactivating/withdrawing their own stake account between the epoch-boundary calculation block and a validator's snapshot slot, with no special privileges needed.

### Likelihood Explanation
Preconditions are realistic and fully within the "unprivileged user" attacker model: deactivate stake, wait for cooldown/withdraw is not even required — simply closing/withdrawing a stake account whose stake was already counted for the current epoch's rewards is a normal user action available at any time, and validator snapshot/restart cadence is common in production fleets. However, exploitability depends on the target validator actually performing (or being forced to perform) a snapshot restore precisely mid-distribution, which is an operational condition outside pure attacker control, and the actual magnitude of divergence (a handful of lamports burned differently, and reshuffled `partition_indices` for the remainder of that epoch's distribution) is bounded by the number of "orphaned" pubkeys the attacker can create before the snapshot slot.

### Recommendation
Persist enough of `EpochRewardStatus` (or a stable derivation of it, e.g., the original stake-pubkey list/order and per-pubkey unpaid amounts) across snapshots so that a restored bank can resume distribution using the exact same `all_stake_rewards` ordering and `partition_indices` as the live bank would have, rather than recomputing `all_stake_rewards` from the live (post-snapshot-slot) `stakes_cache`. At minimum, the recalculation path in `recalculate_stake_rewards` should account for pubkeys that were part of the original `all_stake_rewards` but are no longer present in `stakes_cache`, treating them identically to the live bank's `AccountNotFound`/burn path instead of silently omitting them from the recomputed list.

### Proof of Concept
Rust integration test (add near `test_initialize_after_snapshot_restore` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`):
1. Build a reward bank with ≥2 stakers across 2+ partitions using `create_reward_bank_with_specific_stakes`.
2. Advance to the epoch boundary so `epoch_reward_status` becomes `Active(Calculation(...))`, capturing `expected_all_stake_rewards` and letting the bank derive `partition_indices` for distribution.
3. Fork the test into two lineages at the same slot (mid-distribution, before all partitions are paid):
   - Lineage A ("live"): continue advancing `Bank::new_from_parent` normally through the remaining distribution blocks.
   - Lineage B ("snapshot-restored"): before advancing, have one staker (present in an as-yet-undistributed partition) submit a stake `Deactivate` + `Withdraw` instruction sequence (or directly zero/close via test helper) at the fork slot, simulating attacker churn immediately after the "snapshot slot"; then call `bank.feature_set = ...; bank.initialize_after_snapshot_restore(...)` to simulate the snapshot-restore recalculation, and continue advancing.
4. Assert: `lineage_A.capitalization()` after full distribution completes != `lineage_B.capitalization()`, and/or `lineage_A.hash()` != `lineage_B.hash()` at the same slot — demonstrating that identical starting state plus an attacker-controlled stake-account close produces divergent AccountsDB contents/bank hash depending solely on whether a snapshot restore occurred mid-distribution (SNAPSHOT_FIDELITY violation).

### Citations

**File:** runtime/src/bank.rs (L2178-2182)
```rust
            accounts_data_size_initial,
            accounts_data_size_delta_on_chain: AtomicI64::new(0),
            accounts_data_size_delta_off_chain: AtomicI64::new(0),
            epoch_reward_status: EpochRewardStatus::default(),
            transaction_processor: TransactionBatchProcessor::default(),
```

**File:** runtime/src/bank.rs (L2218-2219)
```rust
        bank.refresh_slot_params_from_snapshot(genesis_config);
        bank.initialize_after_snapshot_restore(|| rewards_calculation_thread_pool);
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1011-1032)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1053-1094)
```rust
        let stakes = self.stakes_cache.stakes();
        let EpochRewardCalculateParamInfo {
            stake_history,
            stake_delegations,
            cached_vote_accounts,
        } = self.get_epoch_params_for_recalculation(rewarded_epoch, &stakes);
        let ag_epoch_type = AlpenglowEpochType::get(self, rewarded_epoch, || {
            RewardEpochDelegatedStakes::get(self)
        });

        // On recalculation, only the `StakeRewardCalculation::stake_rewards`
        // field is relevant. It is assumed that reward commission accounts have
        // already been calculated and delivered, while
        // `StakeRewardCalculation::total_rewards` only reflects rewards that
        // have not yet been distributed.
        //
        // NOTE: the `RewardCommissionAccounts` will NOT have a correct
        // post_lamport amount if the commission account is NOT the vote account,
        // because the commission account is loaded from the current bank, and
        // not the start of the epoch. We don't have a snapshot of all commission
        // accounts from the start of the epoch. For this reason, the
        // `RewardCommissionAccounts` calculated in this function call should
        // NOT be used ever.
        let (_, StakeRewardCalculation { stake_rewards, .. }) = self
            .calculate_stake_rewards_and_commissions(
                &stake_history,
                stake_delegations,
                cached_vote_accounts,
                rewarded_epoch,
                point_value,
                &ag_epoch_type,
                thread_pool,
                null_tracer(),
                &mut RewardsMetrics::default(), // This is required, but not reporting anything at the moment
            );
        drop(stakes);
        let partition_indices = hash_rewards_into_partitions(
            &stake_rewards,
            &epoch_rewards_sysvar.parent_blockhash,
            epoch_rewards_sysvar.num_partitions as usize,
        );
        (stake_rewards, partition_indices)
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2770-2798)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L127-149)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L360-408)
```rust
        let mut updated_stake_rewards = Vec::with_capacity(indices.len());
        let stakes_cache = self.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        let stake_history = stakes_cache.history();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let rent = &self.rent_collector.rent;
        for index in indices {
            let partitioned_stake_reward = partition_rewards
                .all_stake_rewards
                .get(*index)
                .unwrap_or_else(|| {
                    panic!(
                        "partition reward out of bound: {index} >= {}",
                        partition_rewards.all_stake_rewards.total_len()
                    )
                })
                .as_ref()
                .unwrap_or_else(|| {
                    panic!("partition reward {index} is empty");
                });
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L146-158)
```rust
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct StartBlockHeightAndPartitionedRewards {
    /// the block height of the slot at which rewards distribution began
    pub(crate) distribution_starting_block_height: u64,

    /// calculated epoch rewards pending distribution
    pub(crate) all_stake_rewards: Arc<PartitionedStakeRewards>,

    /// indices of calculated epoch rewards per partition, outer Vec is by
    /// partition (one partition per block), inner Vec is the indices for one
    /// partition.
    pub(crate) partition_indices: Vec<Vec<usize>>,
}
```
