### No vulnerability found for this question.

**Analysis supporting this conclusion:**

The claimed equality is: for a tenure, `sum(this_burn_total_p)` over all participants `p` (miner + every user-burn-support record) must equal the shared denominator `burn_total` used by every participant's coinbase-share calculation in `calculate_miner_reward` (`stackslib/src/chainstate/stacks/db/accounts.rs`), so that `sum(MinerReward.total())` never exceeds `coinbase + parent_fees`.

Tracing the code:

- In the miner branch (`participant.miner == true`), the denominator is built unconditionally by summing every entry in `users` with no address filtering: [1](#0-0) 

- In the user branch (`participant.miner == false`), `total_other` is *seeded* with `miner.burnchain_commit_burn` unconditionally — it is never gated on `miner.address != participant.address`: [2](#0-1) 

For the exact precondition in the question — a single `MinerPaymentSchedule{miner:false}` row whose `address` equals the miner's `address`, with no *other* user row sharing that address — the loop `if user_support.address != participant.address` only compares against peer entries in the `users` slice (which excludes the miner's own record, passed separately as `miner: &MinerPaymentSchedule`). Since the miner's burn is added to `total_other` unconditionally regardless of any address match, and exactly one entry in `users` matches `participant.address` (itself), `this_user` is set to that single row's own `burnchain_commit_burn`, and it is excluded from `total_other` exactly once. The resulting `burn_total = this_user + total_other` therefore still equals `miner.burnchain_commit_burn + Σ(all users' burnchain_commit_burn)` — the same total `T` used in the miner's own denominator computation.

So for this specific attacker input (miner-address/user-address collision, no duplicate among the user rows themselves), both sides of the equality match before and after: the per-row `this_burn_total` values still partition `burn_total` correctly, and `coinbase_reward = participant.coinbase * this_burn_total / burn_total` summed over all rows cannot exceed `coinbase` (up to normal floor-division underpayment, never overpayment).

Note: a related but distinct bug does exist in this code — if **two different rows within the `users` slice** share the *same* address as each other (not the miner's address), the `this_user = ...` assignment (not accumulation) combined with the exclusion filter can drop one row's burn entirely and shrink the denominator inconsistently across participants, which could plausibly cause overpayment. However, that is a different precondition (user-user address collision) than the one posed in this question (user-miner address collision), and is out of scope for this specific query. [3](#0-2)

### Citations

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L816-823)
```rust
                // we're calculating the miner's reward
                let mut total_user: u128 = 0;
                for user_support in users.iter() {
                    total_user = total_user
                        .checked_add(user_support.burnchain_commit_burn as u128)
                        .expect("FATAL: user support burn overflow");
                }
                (participant.burnchain_commit_burn as u128, total_user)
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L826-838)
```rust
                let mut this_user: u128 = 0;
                let mut total_other: u128 = miner.burnchain_commit_burn as u128;
                for user_support in users.iter() {
                    if user_support.address != participant.address {
                        total_other = total_other
                            .checked_add(user_support.burnchain_commit_burn as u128)
                            .expect("FATAL: user support burn overflow");
                    } else {
                        this_user = user_support.burnchain_commit_burn as u128;
                    }
                }
                (this_user, total_other)
            }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L1044-1068)
```rust
        let (parent_miner_reward, miner_reward) = StacksChainState::calculate_miner_reward(
            mainnet,
            parent_evaluated_epoch.epoch_id,
            &miner,
            &miner,
            &users,
            &parent_miner,
            poison_recipient_opt.as_ref(),
        );

        // calculate reward for each user-support-burn
        let mut user_rewards = vec![];
        for user_reward in users.iter() {
            let (parent_reward, reward) = StacksChainState::calculate_miner_reward(
                mainnet,
                parent_evaluated_epoch.epoch_id,
                user_reward,
                &miner,
                &users,
                &parent_miner,
                poison_recipient_opt.as_ref(),
            );
            assert_eq!(parent_reward.total(), 0);
            user_rewards.push(reward);
        }
```
