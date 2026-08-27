## Title
Unbounded stake-delegation count enables unpartitioned, synchronous O(n) work at every epoch boundary reward calculation — (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The CoreDAO finding shows that `CandidateHub.register()` has no cap on the number of candidates that can be added to `candidateSet`, so an attacker can inflate that array until the per-epoch `turnRound()` for-loop exceeds the block gas limit and becomes inoperable. Agave has a directly analogous unbounded, ordinary-client-reachable collection: `StakesCache`'s `stake_delegations` map, which is grown by any ordinary user simply creating stake accounts and delegating (`CreateAccount` + `DelegateStake`), with no maximum count enforced anywhere in the protocol.

### Finding Description
Every stake account with `lamports() > 0` owned by the stake program is unconditionally inserted into the `Stakes::stake_delegations` map by `StakesCache::check_and_store` [1](#0-0) , with no limit on how many distinct stake accounts (and thus map entries) may exist. The only "gate" is the per-account minimum delegation (`get_minimum_delegation`, currently 1 SOL when the relevant feature is active) [2](#0-1)  — a capital cost, not a hard cap, exactly analogous to CoreDAO's `requiredMargin` deposit that the report shows is insufficient to stop the array-inflation attack "in principle."

At every epoch boundary, `Bank::process_new_epoch` calls `compute_new_epoch_caches_and_rewards`, which pulls the *entire* `stake_delegations` map into a `Vec` via `stakes.stake_delegations_vec()` [3](#0-2) . Note the comment in the codebase itself acknowledging this concern: *"For N stake delegations, where N is >1,000,000, we produce: N stake rewards..."* [4](#0-3) . This full vector is then iterated over (in parallel via rayon, but still O(n) total work, performed synchronously as part of freezing/rooting the epoch-boundary bank) in `calculate_reward_points_partitioned` [5](#0-4)  and again in `calculate_stake_rewards_and_commissions` [6](#0-5) .

Critically, while Solana's design *does* partition the **distribution** of rewards across many subsequent blocks (`stake_account_stores_per_block`, `num_partitions`, etc.) to avoid overloading any single block — precisely the fix pattern CoreDAO applied (bound the work per call) — the reward **calculation** phase itself is not bounded or partitioned; it must complete in full, synchronously, before the epoch-boundary bank can be considered valid, for every validator in the cluster.

Unlike the vote-account side, where SIMD-357's `clone_and_filter_for_vat`/`MAX_ALPENGLOW_VOTE_ACCOUNTS` truncates the *vote accounts* considered for VAT (an Alpenglow-specific control, out of scope per the analog rules), there is no analogous cap on the *stake_delegations* count feeding the classic (Tower) reward calculation path.

### Impact Explanation
Because every full-node in the cluster (not just the leader) must perform this same unbounded O(n) computation deterministically at every epoch boundary before it can validate/vote on the boundary block, sufficiently inflating `stake_delegations` degrades this critical-path computation for the entire cluster simultaneously — a liveness/availability concern rather than a state-divergence one, directly mirroring the CoreDAO report's concern that `turnRound()` could become "inoperable" for everyone.

### Likelihood Explanation
The per-delegation cost (rent-exempt reserve + 1 SOL minimum delegation) is a real economic deterrent, similar to CoreDAO's `requiredMargin`, which is why the original report itself rated likelihood only 2/5 despite rating impact 4/5. A well-capitalized attacker could still create very large numbers of stake accounts over time using only ordinary, unprivileged `CreateAccount`+`DelegateStake` transactions — there is no protocol-level maximum count anywhere in the codebase.

### Recommendation
Introduce an explicit maximum on the number of stake delegations considered during reward calculation (or a per-epoch/-account count cap analogous to CoreDAO's `CANDIDATE_COUNT_LIMIT`), or extend the SIMD-357-style truncation/filtering approach already used for vote accounts to the `stake_delegations` set feeding `calculate_reward_points_partitioned`/`calculate_stake_rewards_and_commissions`, so the calculation phase — not just the distribution phase — has bounded, partitionable work per epoch boundary.

### Proof of Concept
1. An attacker repeatedly submits ordinary `CreateAccount` (funded to the stake-account rent-exempt minimum) + `DelegateStake` transaction pairs from many keypairs, each meeting only the minimum delegation amount [2](#0-1) .
2. Each such account is unconditionally added to `Stakes::stake_delegations` via `StakesCache::check_and_store` → `upsert_stake_delegation` [1](#0-0) , with no cap on the total count.
3. At the next epoch boundary, `compute_new_epoch_caches_and_rewards` collects the entire (now very large) `stake_delegations_vec()` [3](#0-2)  and every validator must synchronously compute reward points and stake rewards over the full set [6](#0-5)  before the epoch-boundary block can be produced/validated, degrading the whole cluster equally at that boundary.

### Citations

**File:** runtime/src/stakes.rs (L143-153)
```rust
        } else if stake_program::check_id(owner) {
            match StakeAccount::try_from(create_account_shared_data(account)) {
                Ok(stake_account) => {
                    let mut stakes = self.0.write().unwrap();
                    stakes.upsert_stake_delegation(
                        *pubkey,
                        stake_account,
                        new_rate_activation_epoch,
                        use_fixed_point_stake_math,
                    );
                }
```

**File:** runtime/src/stake_utils.rs (L15-27)
```rust
/// The minimum stake amount that can be delegated, in lamports.
/// When this feature is added, it will be accompanied by an upgrade to the BPF Stake Program.
/// NOTE: This is also used to calculate the minimum balance of a delegated stake account,
/// which is the rent exempt reserve _plus_ the minimum stake delegation.
#[inline(always)]
pub fn get_minimum_delegation(upgrade_bpf_stake_program_to_v5_is_active: bool) -> u64 {
    if upgrade_bpf_stake_program_to_v5_is_active {
        const MINIMUM_DELEGATION_SOL: u64 = 1;
        MINIMUM_DELEGATION_SOL * LAMPORTS_PER_SOL
    } else {
        1
    }
}
```

**File:** runtime/src/bank.rs (L1772-1788)
```rust
        let stakes = self.stakes_cache.stakes();
        let stake_delegations = stakes.stake_delegations_vec();
        let (
            (
                stake_history,
                unfiltered_distribution_vote_accounts,
                delegated_stakes,
                reward_epoch_delegated_stakes,
            ),
            calculate_activated_stake_time_us,
        ) = measure_us!(stakes.calculate_activated_stake(
            self.epoch(),
            thread_pool,
            self.new_warmup_cooldown_rate_epoch(),
            &stake_delegations,
            self.use_fixed_point_stake_math(),
        ));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L780-938)
```rust
    fn calculate_stake_rewards_and_commissions<'a>(
        &self,
        stake_history: &StakeHistory,
        stake_delegations: Vec<(&'a Pubkey, &'a StakeAccount<Delegation>)>,
        cached_vote_accounts: CachedVoteAccounts<'_>,
        rewarded_epoch: Epoch,
        point_value: PointValue,
        ag_epoch_type: &AlpenglowEpochType,
        thread_pool: &ThreadPool,
        reward_calc_tracer: Option<impl RewardCalcTracer>,
        metrics: &mut RewardsMetrics,
    ) -> (RewardCommissions, StakeRewardCalculation) {
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let feature_snapshot = self.feature_set.snapshot();
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;
        let delay_commission_updates = feature_snapshot.delay_commission_updates;
        let commission_rate_in_basis_points = feature_snapshot.commission_rate_in_basis_points;
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let custom_commission_collector = feature_snapshot.custom_commission_collector;
        let block_revenue_sharing = feature_snapshot.block_revenue_sharing;

        let mut measure_redeem_rewards = Measure::start("redeem-rewards");
        // For N stake delegations, where N is >1,000,000, we produce:
        // * N stake rewards,
        // * M reward commission accounts, where M is a number of stake nodes.
        //   Currently, way smaller number than 1,000,000. And we can expect it
        //   to always be significantly smaller than number of delegations.
        //
        // Producing the stake reward with rayon triggers a lot of
        // (re)allocations. To avoid that, we allocate it at the start and
        // pass `stake_rewards.spare_capacity_mut()` as one of iterators.
        let stake_delegations_len = stake_delegations.len();
        let mut stake_rewards = PartitionedStakeRewards::with_capacity(stake_delegations_len);
        let rewards_accumulator: RewardsAccumulator = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .zip(&mut stake_rewards.spare_capacity_mut()[..stake_delegations_len])
                .with_min_len(500)
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
                    let maybe_reward_record = self.redeem_delegation_rewards(
                        rewarded_epoch,
                        stake_pubkey,
                        stake_account,
                        &point_value,
                        stake_history,
                        &cached_vote_accounts,
                        reward_calc_tracer.as_ref(),
                        new_warmup_cooldown_rate_epoch,
                        delay_commission_updates,
                        commission_rate_in_basis_points,
                        adjust_delegations_for_rent,
                        ag_epoch_type,
                        custom_commission_collector,
                        use_fixed_point_stake_math,
                    );

                    let (reward, maybe_reward_record) = match (block_reward, maybe_reward_record) {
                        (0, None) => (None, None),
                        (_, Some(res)) => {
                            let InflationRewardWithCommission {
                                inflation,
                                commission_pubkey,
                                reward_commission,
                            } = res;
                            let stake_reward = inflation.stake_reward;
                            (
                                Some(PartitionedStakeReward {
                                    stake_pubkey: **stake_pubkey,
                                    inflation,
                                    block_reward,
                                }),
                                Some(RewardAccumulation {
                                    stake_reward,
                                    commission: Some((commission_pubkey, reward_commission)),
                                }),
                            )
                        }
                        (_, None) => {
                            // Create a zero entry for distribution
                            let stake = *stake_account.stake();
                            let stake_reward = 0;
                            (
                                Some(PartitionedStakeReward {
                                    stake_pubkey: **stake_pubkey,
                                    inflation: InflationReward {
                                        stake,
                                        stake_reward,
                                        commission_bps: None,
                                    },
                                    block_reward,
                                }),
                                // Need a reward record for accumulator
                                Some(RewardAccumulation {
                                    stake_reward,
                                    commission: None,
                                }),
                            )
                        }
                    };
                    // It's important that for every stake delegation, we write
                    // a value to the cell of the stake rewards vector,
                    // regardless of whether it's `Some` or `None` variant.
                    // This allows us to pre-allocate the vector with the known
                    // size and avoid re-allocations, which were the bottleneck
                    // in this path.
                    reward_ref.write(reward);
                    maybe_reward_record
                })
                .fold(
                    RewardsAccumulator::default,
                    |mut rewards_accumulator, accumulation| {
                        rewards_accumulator.add_reward(accumulation);
                        rewards_accumulator
                    },
                )
                .reduce(
                    RewardsAccumulator::default,
                    |rewards_accumulator_a, rewards_accumulator_b| {
                        rewards_accumulator_a.accumulate_into_larger(rewards_accumulator_b)
                    },
                )
        });
        let RewardsAccumulator {
            reward_commissions,
            num_stake_rewards,
            total_stake_rewards_lamports,
        } = rewards_accumulator;
        // SAFETY: We initialized all the `stake_rewards` elements up to
        // `stake_delegations_len` (one cell per delegation, `Some` or `None`).
        // `num_stake_rewards` is the count of the `Some` cells.
        unsafe {
            stake_rewards.assume_init(num_stake_rewards, stake_delegations_len);
        }
        measure_redeem_rewards.stop();
        metrics.redeem_rewards_us = measure_redeem_rewards.as_us();

        (
            reward_commissions,
            StakeRewardCalculation {
                stake_rewards: Arc::new(stake_rewards),
                total_stake_rewards_lamports,
            },
        )
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L942-1009)
```rust
    fn calculate_reward_points_partitioned<'a>(
        &self,
        stake_history: &StakeHistory,
        stake_delegations: &Vec<(&'a Pubkey, &'a StakeAccount<Delegation>)>,
        cached_vote_accounts: &CachedVoteAccounts<'_>,
        epoch_inflation_rewards: u64,
        ag_epoch_type: &AlpenglowEpochType,
        thread_pool: &ThreadPool,
        metrics: &RewardsMetrics,
    ) -> Option<PointValue> {
        let CachedVoteAccounts {
            distribution_epoch_vote_accounts,
            ..
        } = cached_vote_accounts;

        let solana_vote_program: Pubkey = solana_vote_program::id();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        match ag_epoch_type {
            AlpenglowEpochType::Alpenglow { .. } => {
                // In alpenglow, we do not need to compute `PointValue::points` as the final
                // rewards are simply the total credits stored in the vote account.  We just need
                // to return a `Some` value with valid rewards.
                return Some(PointValue {
                    rewards: epoch_inflation_rewards,
                    points: 0,
                });
            }
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
        metrics.calculate_points_us.fetch_add(measure_us, Relaxed);

        (points > 0).then_some(PointValue {
            rewards: epoch_inflation_rewards,
            points,
        })
    }
```
