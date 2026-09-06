### No vulnerability found for this question.

The premise fails: the poison-microblock report's storage key (`height` in `make_microblock_poison_key`) is not attacker-controllable data supplied at maturation time — it is fixed, one-to-one, to the actual block that produced the reported microblock public key hash.

- `handle_poison_microblock` resolves the reporting height via `mblock_pubk_height`, which comes from `get_microblock_pubkey_hash_height(&pubkh)` — a lookup keyed on the actual microblock public key hash used in the conflicting headers [1](#0-0) .
- That `pubkh -> height` mapping is written exactly once per Stacks block, in `finish_block`, using that block's own `block_height` and its own `mblock_pubkey_hash` — i.e., the height is inherently tied to the specific tenure/block that owns the microblock stream, not something the reporter can pick [2](#0-1) .
- `dup.verify(&pubkey_hash)` in `preprocess_streamed_microblock` cryptographically enforces that a poison report's conflicting headers must both verify against the pubkey hash actually registered for that specific ancestor block, so an attacker cannot fabricate a report keyed to an arbitrary height without the corresponding real signing key having actually forked its stream at that height [3](#0-2) .
- At maturation, `find_mature_miner_rewards` computes `reward_height = tip_stacks_height - MINER_REWARD_MATURITY` and looks up `get_poison_microblock_report(clarity_tx, reward_height)` — this is exactly the height of the block/tenure currently maturing, i.e., precisely the same height at which that tenure's own microblock pubkey hash (if any) would have been registered [4](#0-3) .

Because each height has exactly one anchored block in a given fork, and the pubkh-to-height binding is set once per block at `finish_block` time (not attacker-suppliable), there is no way to "replay" a genuine report filed for height H so that it resolves against a different tenure H'. The `get_poison_microblock_report` call in `find_mature_miner_rewards` is a plain key lookup on `reward_height`, and that key was populated deterministically by the honest chain-processing logic for that exact height — cross-height substitution as described in the question is not reachable through this code path.

### Citations

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-803)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
            .get_microblock_pubkey_hash_height(&pubkh)?;
        let current_height = env.global_context.database.get_current_block_height();

        // for the microblock public key hash we had to process
        env.add_memory(20)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        // for the block height we had to load
        env.add_memory(4)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        // was the referenced public key hash used anytime in the past
        // MINER_REWARD_MATURITY blocks?
        let mblock_pubk_height = match microblock_height_opt {
            None => {
                // public key has never been seen before
                let msg = format!(
                    "Invalid Stacks transaction: microblock public key hash {} never seen in this fork",
                    &pubkh
                );
                warn!("{}", &msg;
                      "microblock_pubkey_hash" => %pubkh
                );

                return Err(Error::InvalidStacksTransaction(msg, false));
            }
            Some(height) => {
                if height
                    .checked_add(
                        u32::try_from(MINER_REWARD_MATURITY).expect("FATAL: maturity > 2^32"),
                    )
                    .expect("BUG: too many blocks")
                    < current_height
                {
                    let msg = format!(
                        "Invalid Stacks transaction: microblock public key hash from height {} has matured relative to current height {}",
                        height, current_height
                    );
                    warn!("{}", &msg;
                          "microblock_pubkey_hash" => %pubkh
                    );

                    return Err(Error::InvalidStacksTransaction(msg, false));
                }
                height
            }
        };
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L3451-3473)
```rust
        let pubkey_hash = if let Some(pubkh) = StacksChainState::load_block_pubkey_hash(
            &blocks_tx,
            &blocks_path,
            parent_consensus_hash,
            parent_anchored_block_hash,
        )? {
            pubkh
        } else {
            // don't have the parent
            return Ok(false);
        };

        let mut dup = microblock.clone();
        if let Err(e) = dup.verify(&pubkey_hash) {
            let msg = format!(
                "Invalid microblock {}: failed to verify signature with {}: {:?}",
                microblock.block_hash(),
                pubkey_hash,
                &e
            );
            warn!("{}", &msg);
            return Err(Error::InvalidStacksMicroblock(msg, microblock.block_hash()));
        }
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L5323-5344)
```rust
        // mark microblock public key as used
        match StacksChainState::insert_microblock_pubkey_hash(
            clarity_tx,
            block_height,
            mblock_pubkey_hash,
        ) {
            Ok(_) => {
                debug!(
                    "Added microblock public key {} at height {}",
                    &mblock_pubkey_hash, block_height
                );
            }
            Err(e) => {
                let msg = format!(
                    "Failed to insert microblock pubkey hash {} at height {}: {:?}",
                    &mblock_pubkey_hash, block_height, &e
                );
                warn!("{}", &msg);

                return Err(Error::InvalidStacksBlock(msg));
            }
        }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L985-1041)
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
```
