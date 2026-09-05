### Title
Divergent poison-microblock reward computation for a shared ancestor block across two forks can panic `assert_eq!` in `inner_insert_matured_miner_reward` - ([File: stackslib/src/chainstate/stacks/db/accounts.rs])

### Summary
`StacksChainState::inner_insert_matured_miner_reward` explicitly anticipates that two Stacks forks sharing a common ancestor block will independently recompute and re-insert the same `MinerReward` for a given `(parent_block_id, child_block_id)` pair, and only tolerates this if the two computed rewards are byte-for-byte equal, otherwise it panics via `assert_eq!`. The reward computation performed in `StacksChainState::find_mature_miner_rewards` depends on `StacksChainState::get_poison_microblock_report`, which is queried against the *current fork's* Clarity state at the moment maturation is processed, meaning it can return different results on two forks that share the ancestor being matured but differ in whether a later block includes a `PoisonMicroblock` transaction reporting that ancestor's forked microblocks.

### Finding Description
The claimed equality is: `MinerReward` computed for `(parent_block_id, child_block_id)` on fork A must equal `MinerReward` computed for the identical pair on fork B.

The code path:
- `find_mature_miner_rewards` ( [1](#0-0) ) looks up `poison_recipient_opt` via `StacksChainState::get_poison_microblock_report(clarity_tx, reward_height)`, where `clarity_tx` is the Clarity connection for the **currently processed block/fork**, and `reward_height` is the height of the ancestor block whose reward is maturing (a fixed, shared block across both forks).
- `calculate_miner_reward` ( [2](#0-1) ) branches on `poison_reporter_opt`: if present, the coinbase is redirected (in whole or in part, per `poison_microblock_commission`) to the reporter and the miner is "punished" (zeroed anchored/streamed fees); if absent, the miner receives the full coinbase and fees. This produces two structurally different `MinerReward` values for identical `(parent_block_id, child_block_id)`.
- The resulting reward is inserted via `insert_matured_child_miner_reward` / `insert_matured_parent_miner_reward`, both of which call `inner_insert_matured_miner_reward` ( [3](#0-2) ). This function explicitly comments that "the only time it's okay to re-insert the same reward is if there are two Stacks forks trying to store the same matured rewards for a common ancestor block," and enforces equality with `assert_eq!(rw, reward, "FATAL: tried to insert multiple distinct matured parent block reward records")` (line 515).

Root cause: whether a `PoisonMicroblock` transaction reporting a given ancestor's forked microblock stream is included is a fork-dependent choice (any unprivileged participant can submit the report, and any miner can choose to include or omit it in a given tenure). The poison evidence itself (two conflicting, validly-signed microblocks at the same sequence number from the shared ancestor) exists identically on both forks since it derives from the shared ancestor's microblock stream, but the *report transaction* confirming it is a separate, later transaction that can land in fork A's chain and never land in fork B's chain (or land at a different point relative to the maturity window). Because a node processes/stages multiple candidate forks (this is normal, non-privileged chain-following behavior, not a majority-stake attack), the same ancestor block's reward maturation logic runs independently once per fork once each fork's tip advances `MINER_REWARD_MATURITY` blocks past it. No existing guard (`check_tenure_tx`, `verify_signer_signatures`, `validate_vrf_seed`, static validators, or MARF hashing) constrains the *content* of a later `PoisonMicroblock` report transaction relative to a specific shared ancestor's maturation outcome across forks — those guards validate block/tenure structure, not fork-invariance of maturation math.

### Impact Explanation
If triggered, this is a process panic (`assert_eq!` fatal) on whichever node reprocesses the second fork's maturation for the shared ancestor, which occurs specifically during ordinary fork-following/reorg replay (not an obscure code path — it is the natural way full nodes handle temporary forks). A crashed node cannot advance past the reorg while other nodes (that never observed the second fork's variant sequencing, or that panic differently) may or may not crash depending on the exact order of block arrival, producing an inconsistent/split network view. This matches the "Critical: chain split / deep fork" and "reward mismatch" categories in the rules, since honest nodes following different fork orderings can disagree on liveness (crash vs. no crash) for the same objective chain state.

### Likelihood Explanation
Preconditions:
- An anchored block must have a genuinely forked microblock stream (attacker-producible with a single miner slot, by signing two conflicting microblocks at the same sequence number off that anchor — a capability available to any single miner/leader who mines that tenure).
- The attacker (or any unprivileged participant) then needs the `PoisonMicroblock` report transaction to be included in a following block on one candidate fork but not (yet) on a sibling candidate fork that a node is also tracking, before both forks' tips pass the `MINER_REWARD_MATURITY` threshold relative to the shared ancestor.
- No majority stake, no Sybil, no privileged role is required — this only needs a single miner's block-production choice (whether to include a `PoisonMicroblock` tx) diverging across two forks that a node is legitimately tracking, which is routine during any natural tip competition/reorg window.

This is plausible and repeatable in principle, but I was not able to fully verify all constraints on `PoisonMicroblock` validity (e.g., whether `handle_poison_microblock`/transaction validation in `stackslib/src/chainstate/stacks/db/transactions.rs` restricts the report to a narrow height/tenure window that might make the two forks' outcomes converge before both reach maturity, or whether some other check forces identical inclusion). I was unable to inspect `handle_poison_microblock` fully due to the iteration limit, so I cannot state with certainty whether such a restriction closes this gap.

### Recommendation
Do not `assert_eq!`/panic when two forks disagree on a matured reward for a shared ancestor. Instead, treat a mismatch as a recoverable error (e.g., prefer the reward from the fork that is being extended/is canonical, log and skip re-insertion, or key the `matured_rewards` table by the maturing fork's tip rather than assuming global determinism), and audit whether poison-microblock report validity should be pinned to data intrinsic to the ancestor block itself (not to whichever later block happens to include the report) so the same ancestor always matures to the same reward regardless of fork.

### Proof of Concept
Rust integration test plan (two-fork chainstate harness):
1. Build a `TestChainstateBuilder` and mine anchor block `X` with a microblock stream, then have the miner of `X` sign two conflicting microblocks at the same sequence number (poison evidence).
2. Fork the chain at `X`: build Fork A by mining `MINER_REWARD_MATURITY` blocks on top of `X`, including a `PoisonMicroblock` report transaction (from any address) in one of the early blocks of Fork A that references the conflicting microblocks.
3. Build Fork B similarly (same `MINER_REWARD_MATURITY` block count on top of `X`) but omit the `PoisonMicroblock` report transaction entirely.
4. Process Fork A fully through `StacksChainState::append_block`/tip advance so that `X`'s reward matures and `insert_matured_child_miner_reward` inserts a `MinerReward` for `(parent_block_id(X), child_block_id(X))` reflecting `poison_recipient_opt = Some(reporter)` (assert `coinbase` is the reduced poison-commission amount, not the full coinbase; assert `address == reporter`, not `X`'s miner).
5. Process Fork B fully (simulating the node also tracking/replaying this sibling fork) so that `X`'s reward matures again via the same call path, this time with `poison_recipient_opt = None` (assert this second computed `MinerReward` has `address == X`'s miner and full, un-punished `coinbase`/fees — i.e., structurally unequal to the Fork A reward for the identical pair).
6. Assert that step 5's second `insert_matured_child_miner_reward` call either panics with `"FATAL: tried to insert multiple distinct matured parent block reward records"` (confirming the DoS) or, if a fix is applied, no longer panics and gracefully resolves the conflict — this is the exact assertion pair validating both sides of the claimed (broken) equality.

### Citations

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L497-554)
```rust
    fn inner_insert_matured_miner_reward(
        tx: &mut DBTx<'_>,
        parent_block_id: &StacksBlockId,
        child_block_id: &StacksBlockId,
        reward: &MinerReward,
    ) -> Result<(), Error> {
        // the only time it's okay to re-insert the same reward is if there are two Stacks forks
        // trying to store the same matured rewards for a common ancestor block.
        let cur_rewards = StacksChainState::inner_get_matured_miner_payments(
            tx,
            &parent_block_id.clone().into(),
            &child_block_id.clone().into(),
        )?;
        if !cur_rewards.is_empty() {
            let mut present = false;
            for rw in cur_rewards.iter() {
                if (rw.is_parent() && reward.is_parent()) || (rw.is_child() && reward.is_child()) {
                    // must insert a parent or a child at most once
                    assert_eq!(rw, reward, "FATAL: tried to insert multiple distinct matured parent block reward records");
                    present = true;
                }
            }

            if present {
                return Ok(());
            }
        }

        // not present
        let sql = "INSERT INTO matured_rewards (
            address,
            recipient,
            vtxindex,
            coinbase,
            tx_fees_anchored,
            tx_fees_streamed_confirmed,
            tx_fees_streamed_produced,
            parent_index_block_hash,
            child_index_block_hash
        ) VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9)";

        let args = params![
            reward.address.to_string(),
            reward.recipient.to_string(),
            reward.vtxindex,
            reward.coinbase.to_string(),
            reward.tx_fees_anchored.to_string(),
            reward.tx_fees_streamed_confirmed.to_string(),
            reward.tx_fees_streamed_produced.to_string(),
            parent_block_id,
            child_block_id,
        ];

        tx.execute(sql, args)
            .map_err(|e| Error::DBError(db_error::SqliteError(e)))?;

        Ok(())
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L804-905)
```rust
    fn calculate_miner_reward(
        mainnet: bool,
        parent_block_epoch: StacksEpochId,
        participant: &MinerPaymentSchedule,
        miner: &MinerPaymentSchedule,
        users: &[MinerPaymentSchedule],
        parent: &MinerPaymentSchedule,
        poison_reporter_opt: Option<&StacksAddress>,
    ) -> (MinerReward, MinerReward) {
        ////////////////////// coinbase reward total /////////////////////////////////
        let (this_burn_total, other_burn_total) = {
            if participant.miner {
                // we're calculating the miner's reward
                let mut total_user: u128 = 0;
                for user_support in users.iter() {
                    total_user = total_user
                        .checked_add(user_support.burnchain_commit_burn as u128)
                        .expect("FATAL: user support burn overflow");
                }
                (participant.burnchain_commit_burn as u128, total_user)
            } else {
                // we're calculating a user burn support's reward
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
        };

        let burn_total = other_burn_total
            .checked_add(this_burn_total)
            .expect("FATAL: combined burns exceed u128");

        test_debug!(
            "{}: Coinbase reward = {} * ({}/{})",
            participant.address.to_string(),
            participant.coinbase,
            this_burn_total,
            burn_total
        );

        // in the case of shadow blocks, there will be zero burns.
        // the coinbase is still generated, but it's rendered unspendable
        let (this_burn_total, burn_total) = if burn_total == 0 {
            (1, 1)
        } else {
            (this_burn_total, burn_total)
        };

        // each participant gets a share of the coinbase proportional to the fraction it burned out
        // of all participants' burns.
        let coinbase_reward = participant
            .coinbase
            .checked_mul(this_burn_total)
            .expect("FATAL: STX coinbase reward overflow")
            / burn_total;

        // process poison -- someone can steal a fraction of the total coinbase if they can present
        // evidence that the miner forked the microblock stream.  The remainder of the coinbase is
        // destroyed if this happens.
        let (child_address, child_recipient, coinbase_reward, punished) =
            if let Some(reporter_address) = poison_reporter_opt {
                if participant.miner {
                    // the poison-reporter, not the miner, gets a (fraction of the) reward
                    debug!(
                        "{:?} will recieve poison-microblock commission {}",
                        &reporter_address.to_string(),
                        StacksChainState::poison_microblock_commission(coinbase_reward)
                    );
                    (
                        reporter_address.clone(),
                        reporter_address.to_account_principal(),
                        StacksChainState::poison_microblock_commission(coinbase_reward),
                        true,
                    )
                } else {
                    // users that helped a miner that reported a poison-microblock get nothing
                    (
                        StacksAddress::burn_address(mainnet),
                        StacksAddress::burn_address(mainnet).to_account_principal(),
                        0,
                        false,
                    )
                }
            } else {
                // no poison microblock reported
                (
                    participant.address.clone(),
                    participant.recipient.clone(),
                    coinbase_reward,
                    false,
                )
            };

```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L985-1052)
```rust
    pub fn find_mature_miner_rewards(
        clarity_tx: &mut ClarityTx,
        sortdb_conn: &Connection,
        tip_stacks_height: u64,
        mut latest_matured_miners: Vec<MinerPaymentSchedule>,
        parent_miner: MinerPaymentSchedule,
    ) -> Result<Option<(MinerReward, Vec<MinerReward>, MinerReward, MinerRewardInfo)>, Error> {
        let mainnet = clarity_tx.config.mainnet;
        if tip_stacks_height <= MINER_REWARD_MATURITY {
            // no mature rewards exist
            return Ok(None);
        }

        let reward_height = tip_stacks_height - MINER_REWARD_MATURITY;

        let latest_matured_miners_head = latest_matured_miners
            .first()
            .expect("latest_matured_miners should not be empty");
        assert!(latest_matured_miners_head.vtxindex == 0);
        assert!(latest_matured_miners_head.miner);

        let users = latest_matured_miners.split_off(1);
        let miner = latest_matured_miners
            .pop()
            .expect("BUG: no matured miners despite prior check");

        let reward_info = MinerRewardInfo {
            from_stacks_block_hash: miner.block_hash.clone(),
            from_block_consensus_hash: miner.consensus_hash.clone(),
            from_parent_stacks_block_hash: parent_miner.block_hash.clone(),
            from_parent_block_consensus_hash: parent_miner.consensus_hash.clone(),
        };

        // what epoch was the parent miner's block evaluated in?
        let parent_evaluated_snapshot =
            SortitionDB::get_block_snapshot_consensus(sortdb_conn, &parent_miner.consensus_hash)?
                .expect("FATAL: no snapshot for evaluated block");

        let parent_evaluated_epoch =
            SortitionDB::get_stacks_epoch(sortdb_conn, parent_evaluated_snapshot.block_height)?
                .expect("FATAL: no epoch for evaluated block");

        // was this block penalized for mining a forked microblock stream?
        // If so, find the principal that detected the poison, and reward them instead.
        let poison_recipient_opt =
            StacksChainState::get_poison_microblock_report(clarity_tx, reward_height)?
                .map(|(reporter, _)| reporter);

        if let Some(ref _poison_reporter) = poison_recipient_opt.as_ref() {
            test_debug!(
                "Poison-microblock reporter {} at height {}",
                &_poison_reporter.to_string(),
                reward_height
            );
        } else {
            test_debug!("No poison-microblock report at height {}", reward_height);
        }

        // calculate miner reward
        let (parent_miner_reward, miner_reward) = StacksChainState::calculate_miner_reward(
            mainnet,
            parent_evaluated_epoch.epoch_id,
            &miner,
            &miner,
            &users,
            &parent_miner,
            poison_recipient_opt.as_ref(),
        );
```
