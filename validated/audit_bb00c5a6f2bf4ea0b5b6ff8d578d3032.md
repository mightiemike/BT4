### Title
Stale calculated stake-reward delegation applied to a withdrawn-and-recreated stake account, causing silent delegation/lamport corruption or a deterministic `assert_eq!` panic (cluster halt) - ([File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs])

### Summary
`Bank::build_updated_stake_reward` looks up the *live* `StakeStateV2` for a stake pubkey from `stakes_cache_accounts` at distribution time, but then unconditionally overwrites its delegation with `partitioned_stake_reward.inflation.stake`, a value computed once at the epoch boundary (`calculate_rewards_for_partitioning`) and never invalidated for the rest of the multi-block distribution window. If the account's original delegation is destroyed (withdraw-to-zero purges it from the stakes cache) and the same pubkey is reused to create a brand-new, unrelated `StakeStateV2::Stake` before its scheduled distribution slot, the code applies the stale delegation on top of the new account's live `meta`/lamports, either silently corrupting the new account's delegation or, when `relax_post_exec_min_balance_check` is not active, triggering an `assert_eq!` panic.

### Finding Description
`store_stake_accounts_in_partition` takes a *fresh* snapshot of the stakes cache at the start of each new block during the reward-distribution window: [1](#0-0) . This snapshot reflects whatever the pubkey currently resolves to on-chain, not what it was when rewards were calculated.

However, the reward payload itself — `partitioned_stake_reward.inflation.stake` — is computed once, at the epoch boundary, by `calculate_stake_rewards_and_commissions`/`redeem_delegation_rewards` [2](#0-1) , and is only ever recomputed via `recalculate_partitioned_rewards_if_active`, which is exercised on snapshot restore / test bank construction, not on the normal `new_from_parent` per-block path used during ordinary block production [3](#0-2) . So for the entire distribution window, a given pubkey's `inflation.stake` stays frozen at its calculation-time value.

In `build_updated_stake_reward`, the live account is fetched from the cache, destructured, and then blindly overwritten with the stale value: [4](#0-3) 

- `account`/`meta`/`flags` come from whatever is *currently* stored at that pubkey (the newly created account, if reused).
- `new_stake = partitioned_stake_reward.inflation.stake` is entirely the *old*, calculation-time `Stake { delegation, credits_observed }` (old voter_pubkey, old stake amount, old credits_observed).
- The final `account.set_state(&StakeStateV2::Stake(meta, new_stake, flags))` writes a Frankenstein state: the new account's `meta` (attacker-controlled authorities) combined with the stale delegation belonging to a completely different, already-destroyed stake relationship.

Two concrete outcomes:
1. **Silent corruption / redelegation without consent** (when `adjust_delegations_for_rent` = `relax_post_exec_min_balance_check` is active): `adjust_delegation_for_rent` only clamps `new_stake.delegation.stake` to the account's actual lamports minus rent-exempt reserve [5](#0-4) ; it does not validate `voter_pubkey` or `credits_observed` against the newly created delegation. The recreated account silently ends up delegated to the *old* voter with the old `credits_observed`, never having gone through a real `DelegateStake` instruction for that target.
2. **Deterministic panic / cluster halt** (when that feature is not active): the `else` branch asserts that `stake.delegation.stake + stake_reward == new_stake.delegation.stake`, where `stake` is deserialized from the *current* (recreated) account: [6](#0-5) . Because the current delegation's stake amount is unrelated to the stale calculated value, this `assert_eq!` fails and panics unconditionally (not routed through the function's `Result`/`DistributionError` handling), inside a consensus-critical code path invoked from every validator's `prepare_for_block_execution` → `distribute_partitioned_epoch_rewards` → `distribute_epoch_rewards_in_partition` → `store_stake_accounts_in_partition`. This is deterministic across all validators processing the same block, i.e., a scripted cluster halt.

The code comment at `store_stake_accounts_in_partition` even states the (false) assumption underpinning this bug: *"Because stake accounts are checked in calculation, and further state mutation prevented by stake-program restrictions, there should never be rewards burned"* [7](#0-6)  — this assumption does not hold for full account closure + reinitialization at the same pubkey, since the stake program has no restriction preventing a fully-withdrawn (zero-lamport) account from being reused for an entirely unrelated delegation.

### Impact Explanation
- If `relax_post_exec_min_balance_check` is inactive on a given cluster, an unprivileged attacker can crash all validators processing the block that contains their partition's distribution — a consensus-halting panic (`assert_eq!` failure), matching the "cluster-halting panic" bounty category.
- If that feature is active, the attacker's newly created stake account is silently rewritten with a stale, unrelated delegation (wrong voter, wrong stake amount before clamping, wrong `credits_observed`), corrupting on-chain stake accounting for that pubkey without any transaction that legitimately authorized that delegation — a value-conservation/determinism violation limited to the reused pubkey.

### Likelihood Explanation
- The attacker only needs an ordinary keypair, no privileges: `Withdraw` the full balance (after normal deactivation/cooldown) to purge the account from `stakes_cache`, then `SystemProgram::CreateAccount` + `StakeInstruction::Initialize` + `StakeInstruction::DelegateStake` at the same pubkey, timed to land before that pubkey's assigned partition/block in the *current* epoch's distribution window (partition assignment is public/derivable from `parent_blockhash` hashing once the distribution phase begins).
- The main cost/complexity is timing the withdrawal-then-recreate sequence to land within the specific block range assigned to that pubkey's partition, which is knowable in advance from `hash_rewards_into_partitions`.
- Repeatable every epoch, using the same or new pubkeys.

### Recommendation
`build_updated_stake_reward` should detect delegation identity/continuity, not just presence, before applying a calculation-time reward record. E.g., store an identifier for the exact delegation instance rewarded (e.g., `(voter_pubkey, activation_epoch, credits_observed_at_calculation, original stake amount hash)`) with `PartitionedStakeReward`, and verify it against the current cached `StakeAccount` before overwriting; on mismatch, treat as `AccountNotFound`/burn the reward via the existing `Err` path (or block reuse of the pubkey until the epoch's rewards fully complete). Additionally, replace the `assert_eq!` divergence check with a proper `Result::Err(DistributionError::...)` so an unexpected state divergence burns the reward gracefully rather than panicking the validator.

### Proof of Concept
Rust bank/SVM integration test plan:
1. Set up a bank with an active stake delegation `D` at pubkey `A`, voter `V1`, generate an epoch boundary so `calculate_rewards_for_partitioning` computes and caches a `PartitionedStakeReward` for `A` referencing `V1`/old stake amount, and enter the distribution window (multiple blocks) as in `test_recalculate_stake_rewards`.
2. Before `A`'s assigned partition block, submit transactions from `A`'s controlled keypair: fully `Withdraw` `A` to zero lamports (after normal deactivation), confirm `stakes_cache` no longer contains `A` (`remove_stake_delegation` fired).
3. In the same window, submit `SystemProgram::CreateAccount` + `StakeInstruction::Initialize` + `StakeInstruction::DelegateStake` at pubkey `A`, delegating to a different voter `V2` with a different stake amount.
4. Advance to `A`'s assigned distribution block and call `distribute_partitioned_epoch_rewards`.
5. Assert:
   - With `relax_post_exec_min_balance_check` disabled: the call panics with "stake reward delegation must be consistent with the updated stake account lamport balance".
   - With the feature enabled: read back `A`'s on-chain `StakeStateV2` and assert its `delegation.voter_pubkey`/`stake`/`credits_observed` incorrectly reflect the stale `V1` calculation instead of the freshly submitted `V2` delegation, demonstrating silent corruption.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L55-76)
```rust
fn adjust_delegation_for_rent(
    delegation: &mut Delegation,
    rewarded_epoch: Epoch,
    new_delegation_with_rewards: u64,
    lamports_with_rewards: u64,
    minimum_lamports: u64,
) {
    let new_delegation = std::cmp::min(
        new_delegation_with_rewards,
        lamports_with_rewards.saturating_sub(minimum_lamports),
    );

    if new_delegation != delegation.stake {
        delegation.stake = new_delegation;
        // Deactivate stake if needed. This deactivation is immediate,
        // unlike a requested deactivation which happens at the next epoch
        // boundary
        if new_delegation == 0 {
            delegation.deactivation_epoch = rewarded_epoch;
        }
    }
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-297)
```rust
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();

        let (mut account, stake_state): (AccountSharedData, StakeStateV2) = stake_account.into();
        let StakeStateV2::Stake(meta, stake, flags) = stake_state else {
            // StakesCache only stores accounts where StakeStateV2::delegation().is_some()
            unreachable!(
                "StakesCache entry {:?} failed StakeStateV2 deserialization",
                partitioned_stake_reward.stake_pubkey
            )
        };
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;

        let mut new_stake = partitioned_stake_reward.inflation.stake;
        if adjust_delegations_for_rent {
            let minimum_balance = rent.minimum_balance(account.data().len());
            // The rewarded epoch is right before the distribution epoch
            let rewarded_epoch = distribution_epoch.saturating_sub(1);
            // The entry in `partitioned_stake_reward` contains the rewards,
            // calculated during the calculation phase
            let delegation_with_rewards = new_stake.delegation.stake;
            adjust_delegation_for_rent(
                &mut new_stake.delegation,
                rewarded_epoch,
                delegation_with_rewards,
                account.lamports(),
                minimum_balance,
            );
        } else {
            let expected_delegation = stake
                .delegation
                .stake
                .saturating_add(partitioned_stake_reward.inflation.stake_reward);
            assert_eq!(
                expected_delegation, new_stake.delegation.stake,
                "stake reward delegation must be consistent with the updated stake account \
                 lamport balance"
            );
        }
        account
            .set_state(&StakeStateV2::Stake(meta, new_stake, flags))
            .map_err(|_| DistributionError::UnableToSetState)?;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L330-332)
```rust
    /// Because stake accounts are checked in calculation, and further state
    /// mutation prevents by stake-program restrictions, there should never be
    /// rewards burned.
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L361-365)
```rust
        let stakes_cache = self.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        let stake_history = stakes_cache.history();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let rent = &self.rent_collector.rent;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L820-849)
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
```

**File:** runtime/src/bank.rs (L1970-1999)
```rust
    fn prepare_for_block_execution(
        &mut self,
        parent_epoch: Epoch,
        parent_slot: Slot,
        parent_capitalization: u64,
        parent_block_height: u64,
        reward_calc_tracer: Option<impl RewardCalcTracer>,
    ) -> PrepareBlockExecutionStats {
        let slot = self.slot;

        // Following code may touch AccountsDb, requiring proper ancestors
        let (_, update_epoch_time_us) = measure_us!({
            if parent_epoch < self.epoch() {
                self.process_new_epoch(
                    parent_epoch,
                    parent_slot,
                    parent_capitalization,
                    parent_block_height,
                    reward_calc_tracer,
                );
            } else {
                // Save a snapshot of stakes for use in consensus and stake weighted networking
                let leader_schedule_epoch = self.epoch_schedule().get_leader_schedule_epoch(slot);
                self.update_epoch_stakes(leader_schedule_epoch, None);
            }
        });

        let (_, distribute_rewards_time_us) =
            measure_us!(self.distribute_partitioned_epoch_rewards());

```
