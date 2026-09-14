### Title
Protocol treasury reward silently dropped when the treasury account is also an active validator - (File: `chain/epoch-manager/src/reward_calculator.rs`)

### Summary
`RewardCalculator::calculate_reward` computes the protocol-treasury share and each validator's share into the same `HashMap<AccountId, Balance>` keyed by account id. If the `protocol_treasury_account` happens to also be a staking validator in that epoch, the second `HashMap::insert` for that account overwrites (instead of adding to) the first, so the treasury's share is lost from the returned rewards map while the epoch's total minted amount still includes it — mirroring the Sherlock M-11 pattern where a self-referential beneficiary/pool split silently drops part of the fee.

### Finding Description
`calculate_reward` first inserts the treasury's cut into `res`: [1](#0-0) 

Then, for every validator (including possibly the treasury account itself, if it staked), it computes `reward` and does `res.insert(account_id, reward)`, which **overwrites** any prior entry for that key: [2](#0-1) 

Because `HashMap::insert` replaces the existing value rather than accumulating it, if `account_id == self.protocol_treasury_account` (i.e., the treasury account is itself a registered validator this epoch, which can happen because `protocol_treasury_account` is an ordinary `AccountId` that can submit a `Stake` action like anyone else), the final `res` map entry for that account holds only its stake-weighted validator `reward`, losing the previously inserted `epoch_protocol_treasury` amount entirely.

Meanwhile, `epoch_actual_reward` (the second return value, ultimately used as `minted_amount` and folded into `new_total_supply`) is accumulated correctly and still includes `epoch_protocol_treasury` plus every validator reward, treasury included: [3](#0-2) [4](#0-3) 

The `res` map (as `validator_rewards`) is what actually credits balances in the runtime's `update_validator_accounts`, both for the generic stake-info loop and for the explicit "treasury already handled above" fast path that is skipped whenever the treasury account has `stake_info`: [5](#0-4) [6](#0-5) 

The comment "If protocol treasury stakes, then the rewards was already distributed above" assumes the stake-info loop credited the *full* combined amount, but due to the `HashMap::insert` overwrite it only ever credited the validator's own stake-weighted reward — the treasury slice (`epoch_protocol_treasury`) is never credited to any account, even though it was already counted into `epoch_actual_reward`/`minted_amount`, which increases `total_supply` for the block per the documented supply formula: [7](#0-6) 

This is structurally identical to the Sherlock M-11 root cause: a fee/reward split that assumes two disjoint recipients, but when the "beneficiary" (treasury) coincides with the entity being paid (a validator), one part of the computed split is silently discarded instead of being summed with the other.

### Impact Explanation
`epoch_protocol_treasury` yoctoNEAR of protocol reward per affected epoch is minted into `total_supply` (raising the on-chain total-supply figure recorded in the block header) but never credited to any account balance. This is a permanent, protocol-level loss of funds that should have gone to the treasury account — a divergence between the accounted total supply and the sum of actual account balances, which corresponds to "permanently frozen/lost funds" and a state-transition/economic-accounting defect. Because the computation is deterministic and identical on all honest nodes, it does not cause a chain fork, but it silently and repeatedly (every epoch the treasury validates) destroys treasury income that the protocol economics (`docs/Economics/Economics.md`, `protocol_reward_rate`) intends to allocate.

### Likelihood Explanation
Triggering requires only that the `protocol_treasury_account` (an ordinary `AccountId`, e.g. `"near"`) be submitted as a `Stake` action and be selected as an active validator for an epoch — a standard, unprivileged staking transaction reachable by anyone who controls that account or who could otherwise cause it to be treated as a validator. There is no special guard preventing the treasury account from staking, and the existing test (`reward_calculator.rs:590`, `test_adjust_max_inflation`) uses a validator account distinct from the treasury account, so this overwrite path is not covered by tests.

### Recommendation
In `RewardCalculator::calculate_reward`, when inserting a validator's reward into `res`, accumulate rather than overwrite for the treasury account (e.g., `res.entry(account_id).and_modify(|r| *r = r.checked_add(reward).unwrap()).or_insert(reward)`), or explicitly special-case `account_id == self.protocol_treasury_account` to add `reward` on top of the previously inserted `epoch_protocol_treasury` value before returning `res`.

### Proof of Concept
1. Configure genesis such that `protocol_treasury_account` = `"near"`.
2. Have account `"near"` submit a `Stake` transaction with sufficient stake to become a selected block/chunk producer for an upcoming epoch.
3. At the epoch boundary, `EpochManager` calls `RewardCalculator::calculate_reward` with `validator_block_chunk_stats` containing `"near"`.
   - `res` initially gets `res["near"] = epoch_protocol_treasury` (`reward_calculator.rs:84`).
   - The validator loop then computes `"near"`'s stake-weighted `reward` and executes `res.insert("near", reward)` (`reward_calculator.rs:142`), overwriting the treasury amount.
   - `epoch_actual_reward` still equals `epoch_protocol_treasury + reward` (`reward_calculator.rs:90,143`), and this is what's returned as `minted_amount` and applied to `total_supply` (`core/primitives/src/block.rs:193`).
4. In `runtime/runtime/src/lib.rs::update_validator_accounts`, `"near"`'s locked balance is increased only by `res["near"] = reward` (line 1749-1754); the treasury fast-path at line 1807-1834 is skipped because `"near"` is present in `stake_info`.
5. Result: `total_supply` increases by `epoch_protocol_treasury + reward`, but `"near"`'s balance only increases by `reward` — `epoch_protocol_treasury` yoctoNEAR is permanently unaccounted for/lost every such epoch.

### Citations

**File:** chain/epoch-manager/src/reward_calculator.rs (L78-90)
```rust
        let epoch_protocol_treasury = Balance::from_yoctonear(
            (U256::from(epoch_total_reward.as_yoctonear())
                * U256::from(*protocol_reward_rate.numer() as u64)
                / U256::from(*protocol_reward_rate.denom() as u64))
            .as_u128(),
        );
        res.insert(self.protocol_treasury_account.clone(), epoch_protocol_treasury);
        if num_validators == 0 {
            return (res, Balance::ZERO);
        }
        let epoch_validator_reward =
            epoch_total_reward.checked_sub(epoch_protocol_treasury).unwrap();
        let mut epoch_actual_reward = epoch_protocol_treasury;
```

**File:** chain/epoch-manager/src/reward_calculator.rs (L94-146)
```rust
        for (account_id, stats) in validator_block_chunk_stats {
            let production_ratio =
                get_validator_online_ratio(&stats, online_thresholds.endorsement_cutoff_threshold);
            let average_produced_numer = production_ratio.numer();
            let average_produced_denom = production_ratio.denom();

            let expected_blocks = stats.block_stats.expected;
            let expected_chunks = stats.chunk_stats.expected();
            let expected_endorsements = stats.chunk_stats.endorsement_stats().expected;

            let online_min_numer =
                U256::from(*online_thresholds.online_min_threshold.numer() as u64);
            let online_min_denom =
                U256::from(*online_thresholds.online_min_threshold.denom() as u64);
            // If average of produced blocks below online min threshold, validator gets 0 reward.
            let reward = if average_produced_numer * online_min_denom
                < online_min_numer * average_produced_denom
                || (expected_chunks == 0 && expected_blocks == 0 && expected_endorsements == 0)
            {
                Balance::ZERO
            } else {
                // cspell:ignore denum
                let stake = *validator_stake
                    .get(&account_id)
                    .unwrap_or_else(|| panic!("{} is not a validator", account_id));
                // Online reward multiplier is min(1., (uptime - online_threshold_min) / (online_threshold_max - online_threshold_min).
                let online_max_numer =
                    U256::from(*online_thresholds.online_max_threshold.numer() as u64);
                let online_max_denom =
                    U256::from(*online_thresholds.online_max_threshold.denom() as u64);
                let online_numer =
                    online_max_numer * online_min_denom - online_min_numer * online_max_denom;
                let mut uptime_numer = (average_produced_numer * online_min_denom
                    - online_min_numer * average_produced_denom)
                    * online_max_denom;
                let uptime_denum = online_numer * average_produced_denom;
                // Apply min between 1. and computed uptime.
                uptime_numer =
                    if uptime_numer > uptime_denum { uptime_denum } else { uptime_numer };
                Balance::from_yoctonear(
                    (U512::from(epoch_validator_reward.as_yoctonear())
                        * U512::from(uptime_numer)
                        * U512::from(stake.as_yoctonear())
                        / U512::from(uptime_denum)
                        / U512::from(total_stake.as_yoctonear()))
                    .as_u128(),
                )
            };
            res.insert(account_id, reward);
            epoch_actual_reward = epoch_actual_reward.checked_add(reward).unwrap();
        }
        (res, epoch_actual_reward)
    }
```

**File:** runtime/runtime/src/lib.rs (L1748-1755)
```rust
            if let Some(mut account) = account {
                if let Some(reward) = validator_accounts_update.validator_rewards.get(account_id) {
                    tracing::debug!(target: "runtime", %account_id, %reward, locked = %account.locked(), "account adding reward to stake");
                    let locked = account.locked().checked_add(*reward).ok_or_else(|| {
                        RuntimeError::UnexpectedIntegerOverflow("update_validator_accounts".into())
                    })?;
                    account.set_locked(locked).or_inconsistent_state(account_id)?;
                }
```

**File:** runtime/runtime/src/lib.rs (L1807-1834)
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
```

**File:** protocol-model/spec/economics.md (L40-41)
```markdown
### 3. Total-supply change per block
`new_total_supply = prev.total_supply + minted_amount − balance_burnt` (`core/primitives/src/block.rs:193`). `minted_amount` is `Some` only on the first block of an epoch, taken from the epoch info populated by `calculate_reward` (`chain/chain/src/chain.rs:2519`). `balance_burnt` is the sum of each included chunk's `prev_balance_burnt()` (`block.rs:152`). Thus inflation *adds* to supply once per epoch, and burnt fees *subtract* every block; the difference is net issuance.
```
