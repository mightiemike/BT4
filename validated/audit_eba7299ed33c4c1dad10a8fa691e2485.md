### Title
Fork-dependent poison-microblock report causes `assert_eq!` FATAL panic (and possible inconsistent reward crediting) in `inner_insert_matured_miner_reward` - (File: stackslib/src/chainstate/stacks/db/accounts.rs)

### Summary
`StacksChainState::find_mature_miner_rewards` computes the recipient of a matured miner reward by consulting `get_poison_microblock_report` against the *currently-processing fork's* Clarity/MARF state, not against any property intrinsic to the ancestor tenure being matured. `inner_insert_matured_miner_reward`, however, assumes that the reward for a given `(parent_block_id, child_block_id)` ancestor pair is deterministic across forks and will `assert_eq!`-panic ("FATAL: tried to insert multiple distinct matured parent block reward records") if two forks compute different `MinerReward` values for the same ancestor. Because poison-microblock report visibility is fork-local, two sibling forks that share a common ancestor tenure but differ in whether a poison report for that tenure has been mined can legitimately compute different `MinerReward.recipient` values, crashing the node.

### Finding Description
Broken equality: reward recipient(fork A) should equal reward recipient(fork B) for the same matured ancestor tenure `(parent_block_id, child_block_id)`; this equality is what the FATAL assert in `inner_insert_matured_miner_reward` implicitly relies on: [1](#0-0) 

The reward that gets inserted, however, is not solely a function of the ancestor block/tenure. In `find_mature_miner_rewards`, the poison-microblock recipient lookup is performed against `clarity_tx`, which is scoped to the block/tenure currently being processed on whichever fork is executing: [2](#0-1) 

`get_poison_microblock_report` reads this from the Clarity DB (a fork-local MARF trie), so its result differs depending on which fork's block history actually contains the poison-report transaction for `reward_height`: [3](#0-2) 

`calculate_miner_reward` then branches on `poison_reporter_opt` and swaps `child_address`/`child_recipient` from the miner to the poison reporter when present, directly changing `MinerReward.recipient`: [4](#0-3) 

Because `matured_rewards` (and `payments`) live in the shared chainstate sqlite DB — not in the per-block MARF trie — entries for a given `(parent_block_id, child_block_id)` key are visible to, and re-checked by, every fork that shares that ancestor. The code comment even anticipates "two Stacks forks trying to store the same matured rewards for a common ancestor block" as a legitimate, expected case, and assumes it is always safe because the ancestor's own recorded transactions are identical on both forks. That assumption is false once poison-microblock reporting is factored in: the poison report is *not* stored in the ancestor tenure's own blocks — it can be filed as an ordinary transaction in any later block, and later blocks legitimately differ between sibling forks. So:
- Fork A matures the ancestor tenure without having ever seen a poison report for it → inserts `MinerReward{recipient: miner}`.
- Fork B matures the *same* ancestor tenure but had a poison-report transaction mined into one of its own (fork-B-only) later blocks → computes `MinerReward{recipient: poison_reporter}`.
- When fork B's block processing calls `insert_matured_child_miner_reward` → `inner_insert_matured_miner_reward`, it finds fork A's earlier row for the same key, and `assert_eq!(rw, reward, ...)` panics because `recipient` differs.

No existing guard prevents this: `check_tenure_tx`, VRF/static block validation, and `common_validate_against_burnchain` all validate *this* block/tenure's own consensus-critical fields, but they do not, and cannot, constrain what poison-report state exists on a *sibling* fork at the moment the ancestor's reward matures — that is inherently fork-local Clarity state read at maturation time, long after the ancestor tenure itself was already accepted on both forks.

### Impact Explanation
The immediate, reproducible impact is a FATAL process panic (`assert_eq!`) triggered deep inside miner-reward maturation logic, executed while holding an in-progress block-processing/chainstate transaction — this is a liveness-loss crash on any node that tracks both sibling forks (common in normal fork-tracking/fork-choice operation). If the panic occurs after `account_credit`/`ClarityTx` for the reward has already been applied and committed on one fork's MARF trie before the sibling fork's insert panics, the crashed node is left with a chainstate sqlite DB that has recorded only one fork's `matured_rewards` row while its process has aborted mid-transaction — a genuinely inconsistent on-disk state requiring recovery. This matches "Critical: crash = liveness loss on affected nodes, and if the crash occurs post-commit, an inconsistent STX credit persists on disk" from the prompt's scoped impact.

### Likelihood Explanation
Preconditions: (1) two sibling forks sharing a common ancestor tenure (a routine occurrence — microblock forks, tenure-change races, or brief chain-tip disagreements are expected/normal chain operation, not an attacker privilege); (2) a poison-microblock report transaction for the offending tenure that is broadcast/mined into a block on only one of the two forks. Filing a poison-microblock report requires only presenting two conflicting, validly-signed microblocks — obtainable by any participant (including a single miner slot equivocating its own microblock stream, or an attacker simply submitting the report tx with asymmetric propagation so it lands in only one fork's mempool/block). No majority stake, no signer collusion, and no privileged role is required — this is exactly the "unprivileged attacker broadcasting txs and extending forks" threat model in scope. The bug is deterministically repeatable given these preconditions.

### Recommendation
Make matured-reward maturation deterministic per-ancestor regardless of which fork observes it, e.g., resolve poison-microblock reports against the ancestor tenure's own canonical/fork-independent state (or record the ancestor's poison-report resolution at the time the offending tenure itself is closed out, not lazily at maturation time from the current fork's Clarity DB). Alternatively, key `matured_rewards` rows by `(parent_block_id, child_block_id, tip_block_id)` so that divergent per-fork computations do not collide, and replace the `assert_eq!` FATAL panic with a non-fatal, per-fork-scoped resolution path.

### Proof of Concept
Rust integration test plan (two-fork harness in `stackslib/src/chainstate/stacks/db/accounts.rs` or a Nakamoto two-fork test module):
1. Build a common tenure chain up to tenure `T` (ancestor `parent_block_id`/`child_block_id`), with a microblock stream produced by miner `M` that is forkable (two conflicting microblocks at the same sequence number exist, signed by `M`).
2. Fork the chain into fork A and fork B at the block immediately after `T`.
3. On fork B only, have a participant broadcast and get mined a `TransactionPayload::PoisonMicroblock` transaction reporting `M`'s conflicting microblocks for tenure `T`.
4. On fork A, mine forward `MINER_REWARD_MATURITY` blocks with no poison report; call the code path that invokes `find_mature_miner_rewards`/`insert_matured_child_miner_reward` for tenure `T`, capture `reward_A = get_matured_miner_payment(..., parent_block_id, child_block_id)`. Assert `reward_A.recipient == M.to_account_principal()`.
5. On fork B, mine forward `MINER_REWARD_MATURITY` blocks (including the poison-report block) reusing the same underlying `StacksChainState`/sqlite headers DB; trigger maturation for the same `(parent_block_id, child_block_id)`.
6. Assert the equality the question claims should hold: `reward_A.recipient == reward_B.recipient`. Show this assertion fails (`reward_B.recipient == poison_reporter`, not `M`), and that fork B's `insert_matured_child_miner_reward` → `inner_insert_matured_miner_reward` hits `assert_eq!(rw, reward, "FATAL: tried to insert multiple distinct matured parent block reward records")`, panicking the test process before completing block processing — demonstrating the crash and the broken cross-fork determinism.

### Citations

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L503-523)
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
        }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L695-704)
```rust
    pub fn get_poison_microblock_report<T: ClarityConnection>(
        clarity_tx: &mut T,
        height: u64,
    ) -> Result<Option<(StacksAddress, u16)>, Error> {
        let principal_seq_opt = clarity_tx
            .with_clarity_db_readonly(|ref mut db| db.get_microblock_poison_report(height as u32))
            .map_err(|e| Error::ClarityError(e.into()))?;

        Ok(principal_seq_opt.map(|(principal, seq)| (principal.into(), seq)))
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L872-904)
```rust
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

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L1027-1052)
```rust
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
