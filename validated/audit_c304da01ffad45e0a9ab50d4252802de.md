### Title
Poison-microblock report status is fork-dependent but keyed only by block height, causing `inner_insert_matured_miner_reward`'s `assert_eq!` to panic (or silently diverge) when two forks independently mature the same shared child block - ([File: stackslib/src/chainstate/stacks/db/accounts.rs])

### Summary
`StacksChainState::find_mature_miner_rewards` determines whether a coinbase reward is redirected to a poison-microblock reporter by calling `get_poison_microblock_report(clarity_tx, reward_height)`, which looks up a Clarity DB entry keyed **only by block height** in the currently-processing fork's MARF state, not by any value bound to the specific `(parent_block_id, child_block_id)` pair being rewarded. Two forks that share a common ancestor chain up to and including the matured child block, but diverge afterward, can therefore compute two different `MinerReward` values for the *same* `(parent_block_id, child_block_id)` pair if only one of the post-divergence chains ever confirmed a poison-microblock report transaction for that height. When a node processes both forks (e.g. during a reorg replay), `inner_insert_matured_miner_reward` will attempt to insert the second, differing reward and hit `assert_eq!(rw, reward, "FATAL: tried to insert multiple distinct matured parent block reward records")`, crashing the process.

### Finding Description
The broken equality is:

`MinerReward` computed by fork A for `(parent_block_id, child_block_id)` == `MinerReward` computed by fork B for the identical `(parent_block_id, child_block_id)`.

The code path:
- `find_mature_miner_rewards` computes `reward_height = tip_stacks_height - MINER_REWARD_MATURITY` and looks up `poison_recipient_opt` via `get_poison_microblock_report(clarity_tx, reward_height)`: [1](#0-0) . This lookup is a Clarity DB read against **whatever fork's MARF state is currently open** (`clarity_tx`) at that height, not a value derived from or committed to the `child_block_id` itself.
- `calculate_miner_reward` uses `poison_reporter_opt` to conditionally redirect coinbase to a reporter and zero-out tx fees, directly changing the `coinbase`, `address`, and `recipient` fields of the resulting `MinerReward`: [2](#0-1) .
- The resulting reward is persisted keyed by `(parent_block_id, child_block_id)` via `insert_matured_child_miner_reward` → `inner_insert_matured_miner_reward`, which fetches any pre-existing reward for that exact pair and asserts equality before accepting a re-insert: [3](#0-2) .

Root cause: the poison-report lookup is bound to a chain-relative height on the currently active fork, not to the immutable content of the specific child block being rewarded. If block `C` (with fixed `StacksBlockId` = `child_block_id`) is a common ancestor of forks A and B, and a poison-microblock report transaction naming the microblock-forking miner at `C`'s tenure height is confirmed in a later block on fork A but never confirmed (or confirmed differently) on fork B, then when each fork's tip matures block `C`'s reward (100 blocks later on each fork respectively), `calculate_miner_reward` returns different `coinbase`/`address` values for the identical `(parent_block_id, child_block_id)` pair.

An unprivileged attacker (per the stated threat model, capable of "filing poison reports and extending forks") can engineer this: mine or observe a microblock fork at the shared ancestor's tenure, then submit a poison-microblock-report transaction that only gets confirmed on one of two competing forks before each fork matures the shared block independently. No existing guard (`check_tenure_tx`, VRF/static validators, `common_validate_against_burnchain`, MARF hashing) constrains this, because the poison-report bookkeeping is a plain Clarity-DB write performed during ordinary transaction processing, entirely independent from block-validity checks, and the maturation window (`MINER_REWARD_MATURITY`) does nothing to bind the poison determination to the specific child block's index hash.

### Impact Explanation
On the node that processes both forks (a normal reorg-replay scenario), the second reward computation triggers the `assert_eq!` panic in `inner_insert_matured_miner_reward`, crashing the process. This is a Critical impact under the stated criteria: it manifests as a chain split, since the crashed node cannot advance past the reorg while other nodes (which may only ever have observed one of the two forks) continue; and if the assert were ever bypassed, the actual reward paid would differ from fork to fork for the same historical block, which is a reward mismatch/loss scenario ("block-reward theft/double-payment/loss" is explicitly listed as Critical).

### Likelihood Explanation
The preconditions are: (1) two forks that share a common tenure/block as ancestor, one of which confirms a poison-microblock report transaction for that ancestor's tenure height and the other does not (or confirms a different reporter); (2) both forks independently advance at least `MINER_REWARD_MATURITY` blocks past the shared ancestor so each matures the reward on its own tip; (3) a node processes both forks (ordinary during any reorg). None of this requires majority stake — submitting a poison-microblock report is a normal, permissionless transaction, and producing a short-lived divergent fork requires only a minority mining position or a single sortition win, consistent with the stated unprivileged-attacker model. The main cost is producing/maintaining two forks long enough (100+ blocks each) for both to mature independently, which is nontrivial but not privileged.

### Recommendation
Bind the poison-microblock-report lookup (and any other maturation-time side data used to compute `MinerReward`) to the specific `child_block_id`/tenure being matured rather than to a height on the currently-open fork's MARF — e.g., store/verify the poison report against the exact index-block-hash of the tenure it forks, or re-derive determinism by validating that the report was confirmed on the same chain history as the child block being rewarded before allowing it to affect the reward. Additionally, `inner_insert_matured_miner_reward` should not `panic!` on a mismatch; it should return a recoverable `Error` so a reorg-replay divergence degrades gracefully instead of crashing the process.

### Proof of Concept
Rust integration test plan (two-fork chainstate harness):
1. Build a chainstate with a common tenure `C` (child block) with `parent_block_id`, `child_block_id` fixed.
2. Fork A: after `C`, include a valid poison-microblock report transaction (two conflicting microblocks signed by `C`'s miner key at the same sequence number) in a subsequent block; advance fork A by `MINER_REWARD_MATURITY + 1` blocks so `find_mature_miner_rewards` matures `C`'s reward with `poison_recipient_opt = Some(reporter)`. Assert `reward_a.coinbase != 0 && reward_a.address == reporter_address`.
3. Fork B: branch from the same ancestor as `C` (reusing identical `parent_block_id`/`child_block_id`) but never confirm any poison report; advance fork B by `MINER_REWARD_MATURITY + 1` blocks so `find_mature_miner_rewards` matures `C`'s reward with `poison_recipient_opt = None`. Assert `reward_b.coinbase != 0 && reward_b.address == C's original miner address`.
4. Assert the equality under test: `reward_a != reward_b` for the identical `(parent_block_id, child_block_id)` (confirming the divergence).
5. On a single `StacksChainState`, call `insert_matured_child_miner_reward(tx, &parent_block_id, &child_block_id, &reward_a)` then, simulating a reorg replay, call `insert_matured_child_miner_reward(tx, &parent_block_id, &child_block_id, &reward_b)`, and assert this second call panics with `"FATAL: tried to insert multiple distinct matured parent block reward records"` (confirming the crash / DoS-via-reorg path).

### Citations

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L503-522)
```rust
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
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L869-904)
```rust
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

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L1029-1031)
```rust
        let poison_recipient_opt =
            StacksChainState::get_poison_microblock_report(clarity_tx, reward_height)?
                .map(|(reporter, _)| reporter);
```
