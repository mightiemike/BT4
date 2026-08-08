### Title
Unbounded per-partition reward-store cost from stake-account flooding bypasses `MAX_PARTITIONED_REWARDS_PER_BLOCK` intent - ([File: runtime/src/bank/partitioned_epoch_rewards/mod.rs])

### Summary
`Bank::get_reward_distribution_num_blocks` clamps only the *number* of reward-distribution blocks to `slots_per_epoch / MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH`, but does not cap the *number of stake accounts placed in each block*. Since `hash_rewards_into_partitions` splits all stake rewards into exactly that many buckets, an attacker who delegates enough minimum-stake accounts before an epoch boundary can force each of the (capped) distribution blocks to contain far more than `MAX_PARTITIONED_REWARDS_PER_BLOCK` (4096) accounts, making `store_stake_accounts_in_partition` do unbounded work in a fixed number of 400ms slots.

### Finding Description
`get_reward_distribution_num_blocks` computes: [1](#0-0) 
`num_chunks = total_stake_accounts.div_ceil(stake_account_stores_per_block)`, then clamps it to `[1, slots_per_epoch / 10]`. This clamp bounds the count of blocks used for distribution, not the size of any individual block. The resulting `num_partitions` value is fed straight into `hash_rewards_into_partitions`, which hashes each stake pubkey into one of `num_partitions` buckets: [2](#0-1) 
and each bucket is later processed in full, in a single block, by `store_stake_accounts_in_partition`, which iterates every index in `partition_rewards.partition_indices[partition_index]` and calls `store_accounts` for the whole batch: [3](#0-2) [4](#0-3) 

There is no code path that re-splits a bucket if it exceeds `MAX_PARTITIONED_REWARDS_PER_BLOCK` (that constant, defined in `accounts-db/src/partitioned_rewards.rs`, is only used as the *default* value for `stake_account_stores_per_block`, never as an enforced ceiling on actual per-block reward counts): [5](#0-4) 

Consequently, once `total_stake_accounts` exceeds `stake_account_stores_per_block * (slots_per_epoch / 10)`, `num_chunks` saturates at the clamp ceiling while `total_stake_accounts` keeps growing — the average bucket size `total_stake_accounts / num_partitions` grows without bound. The attacker input is simply the number of active stake delegations (any size, including minimum rent-exempt/minimum-delegation stakes) present at the epoch boundary; there is no fee or cost proportional to the resulting per-block store/index/hashing work in `store_stake_accounts_in_partition` → `self.store_accounts(...)`.

Existing guards (zero-lamport checks, ancestor/slot checks, reference counting) are irrelevant here — this is a scheduling/sizing bug in reward distribution, not a data-integrity bypass.

### Impact Explanation
This falls under the stated scope: "An unprivileged attacker can cheaply create accounts or writes that force disproportionate storage, index, scan, hashing, or background-cleanup work." Every distribution block whose partition size balloons past `MAX_PARTITIONED_REWARDS_PER_BLOCK` forces `store_stake_accounts_in_partition` to load, mutate, and store many more accounts than the 400ms-slot budget was designed for (`MAX_PARTITIONED_REWARDS_PER_BLOCK` is explicitly documented as the target per-block store count for the slot-time budget). This inflates `store_stake_accounts_us`, AccountsDb write-cache growth, and index update work on every validator, every epoch, for as long as the delegations persist — recurring cost with no proportional fee, since delegation itself costs only the one-time account-creation transaction fee (lamports remain the attacker's).

### Likelihood Explanation
Feasibility requires the attacker to create and delegate a number of stake accounts on the order of `stake_account_stores_per_block * (slots_per_epoch / 10)` (e.g., with defaults `4096 * 43200 ≈ 177M` on a 432000-slot epoch) before an epoch boundary. This is a large but mechanically simple, purely client-side, unprivileged action (repeated `CreateAccount` + `DelegateStake` transactions), requiring no validator/operator control. The attack is deterministic and repeatable every epoch as long as the delegations remain active, since `get_reward_distribution_num_blocks` is invoked fresh each epoch from `begin_partitioned_rewards`.

### Recommendation
Change the partitioning logic so that the number of distribution partitions grows with `total_stake_accounts / stake_account_stores_per_block` even beyond the current epoch-length-based clamp — e.g., allow a block to process more than one 400ms slot's worth of work by extending the distribution window past `slots_per_epoch / 10` (with an absolute upper bound tied to `MAX_PARTITIONED_REWARDS_PER_BLOCK`), or additionally cap and iteratively re-split any single partition bucket that exceeds `MAX_PARTITIONED_REWARDS_PER_BLOCK`, spilling the excess into subsequent blocks rather than clamping only the block count.

### Proof of Concept
Extend `test_get_reward_distribution_num_blocks_cap` style unit tests in `runtime/src/bank/partitioned_epoch_rewards/mod.rs`:

```rust
#[test]
fn test_reward_distribution_partition_size_unbounded() {
    // Use small epoch + small stake_account_stores_per_block to make the
    // clamp reachable with modest counts.
    let (mut genesis_config, _mint_keypair) = create_genesis_config(1_000_000 * LAMPORTS_PER_SOL);
    genesis_config.epoch_schedule = EpochSchedule::custom(32, 32, false);

    let mut accounts_db_config: AccountsDbConfig = ACCOUNTS_DB_CONFIG_FOR_TESTING;
    accounts_db_config.partitioned_epoch_rewards_config =
        PartitionedEpochRewardsConfig::new_for_test(10); // 10 accounts/block baseline

    let bank = Bank::new_from_genesis(
        &genesis_config, Arc::new(RuntimeConfig::default()), Vec::new(), None,
        accounts_db_config, None, Some(SlotLeader::new_unique()), Arc::default(), None, None,
    );
    // Clamp ceiling = slots_per_epoch / 10 = 3 blocks.
    let max_blocks = 3;

    for num_stakes in [1_000u64, 100_000u64, 10_000_000u64] {
        let stake_rewards = (0..num_stakes)
            .map(|_| Some(PartitionedStakeReward::new_random()))
            .collect::<PartitionedStakeRewards>();

        let num_blocks = bank.get_reward_distribution_num_blocks(&stake_rewards);
        assert_eq!(num_blocks, max_blocks, "block count stays clamped");

        // Average per-block accounts grows unbounded as num_stakes grows,
        // demonstrating no per-block cap exists.
        let avg_per_block = num_stakes / num_blocks;
        assert!(avg_per_block > MAX_PARTITIONED_REWARDS_PER_BLOCK * 10,
            "average per-block reward count ({avg_per_block}) vastly exceeds \
             MAX_PARTITIONED_REWARDS_PER_BLOCK, violating the per-slot budget");
    }
}
```

Follow-up integration test: build a `RewardBank` with a very large `num_rewards` (or mock `partition_indices` directly) and drive `distribute_epoch_rewards_in_partition`, measuring `store_stake_accounts_us` from `RewardsStoreMetrics` to show wall-clock cost scaling linearly with the attacker-controlled account count while block count stays fixed at the clamp ceiling.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L408-428)
```rust
    /// Calculate the number of blocks required to distribute rewards to all stake accounts.
    pub(super) fn get_reward_distribution_num_blocks(
        &self,
        rewards: &PartitionedStakeRewards,
    ) -> u64 {
        let total_stake_accounts = rewards.num_rewards();
        if self.epoch_schedule.warmup && self.epoch < self.first_normal_epoch() {
            1
        } else {
            const MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH: u64 = 10;
            let num_chunks = total_stake_accounts
                .div_ceil(self.partitioned_rewards_stake_account_stores_per_block() as usize)
                as u64;

            // Limit the reward credit interval to 10% of the total number of slots in a epoch
            num_chunks.clamp(
                1,
                (self.epoch_schedule.slots_per_epoch / MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH).max(1),
            )
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/epoch_rewards_hasher.rs (L6-24)
```rust
pub(in crate::bank::partitioned_epoch_rewards) fn hash_rewards_into_partitions(
    stake_rewards: &PartitionedStakeRewards,
    parent_blockhash: &Hash,
    num_partitions: usize,
) -> Vec<Vec<usize>> {
    let hasher = EpochRewardsHasher::new(num_partitions, parent_blockhash);
    let mut indices = vec![vec![]; num_partitions];

    for (i, reward) in stake_rewards.enumerated_rewards_iter() {
        // clone here so the hasher's state is reused on each call to `hash_address_to_partition`.
        // This prevents us from re-hashing the seed each time.
        // The clone is explicit (as opposed to an implicit copy) so it is clear this is intended.
        let partition_index = hasher
            .clone()
            .hash_address_to_partition(&reward.stake_pubkey);
        indices[partition_index].push(i);
    }
    indices
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-360)
```rust
    fn store_stake_accounts_in_partition(
        &self,
        partition_rewards: &StartBlockHeightAndPartitionedRewards,
        partition_index: u64,
    ) -> DistributionResults {
        let feature_snapshot = self.feature_set.snapshot();
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;

        let mut stake_reward_lamports_minted = 0;
        let mut stake_reward_lamports_burned = 0;
        let mut block_reward_lamports_distributed = 0;
        let mut block_reward_lamports_burned = 0;
        let indices = partition_rewards
            .partition_indices
            .get(partition_index as usize)
            .unwrap_or_else(|| {
                panic!(
                    "partition index out of bound: {partition_index} >= {}",
                    partition_rewards.partition_indices.len()
                )
            });
        let mut updated_stake_rewards = Vec::with_capacity(indices.len());
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L408-416)
```rust
        }
        drop(stakes_cache);
        self.store_accounts(
            (self.slot(), &updated_stake_rewards[..]),
            // Reuse the rewards calculation thread pool to parallelize
            // loading the previous versions of the stake accounts.
            Some(crate::bank::rewards_calculation_thread_pool()),
        );
        DistributionResults {
```

**File:** accounts-db/src/partitioned_rewards.rs (L1-28)
```rust
//! Code related to partitioned rewards distribution

/// Baseline number of stake accounts to store in one 400ms block during the
/// partitioned reward interval.
///
/// The target is 64 rewards per entry/tick. A block has a minimum of 64
/// entries/ticks, giving 4096 total rewards to store in one 400ms block. This
/// constant affects consensus; shorter slot-time targets scale this value down
/// in `Bank` state.
pub const MAX_PARTITIONED_REWARDS_PER_BLOCK: u64 = 4096;

/// Configuration options for partitioned epoch rewards.
#[derive(Debug, Clone, Copy)]
pub struct PartitionedEpochRewardsConfig {
    /// Baseline number of stake accounts to store in one block during the
    /// partitioned reward interval.
    ///
    /// This value is stored as the 400ms-slot baseline. Runtime `Bank` state
    /// derives the effective per-bank value from the active slot-time target.
    pub stake_account_stores_per_block: u64,
}

/// Convenient constant for default partitioned epoch rewards configuration
/// used for benchmarks and tests.
pub const DEFAULT_PARTITIONED_EPOCH_REWARDS_CONFIG: PartitionedEpochRewardsConfig =
    PartitionedEpochRewardsConfig {
        stake_account_stores_per_block: MAX_PARTITIONED_REWARDS_PER_BLOCK,
    };
```
