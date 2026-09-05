[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L436-437)
```rust
        let index_block_hash =
            StacksBlockId::new(&block_reward.consensus_hash, &block_reward.block_hash);
```

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

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L721-747)
```rust
    pub fn get_scheduled_block_rewards_in_fork_at_height(
        tx: &mut StacksDBTx<'_>,
        tip: &StacksHeaderInfo,
        block_height: u64,
    ) -> Result<Vec<MinerPaymentSchedule>, Error> {
        let ancestor_info = match StacksChainState::get_tip_ancestor(tx, tip, block_height)? {
            Some(info) => info,
            None => {
                test_debug!("No ancestor at height {}", block_height);
                return Ok(vec![]);
            }
        };

        let qry = "SELECT * FROM payments WHERE block_hash = ?1 AND consensus_hash = ?2 ORDER BY vtxindex ASC".to_string();
        let args = params![
            ancestor_info.anchored_header.block_hash(),
            ancestor_info.consensus_hash,
        ];
        let rows = query_rows::<MinerPaymentSchedule, _>(tx, &qry, args).map_err(Error::DBError)?;
        test_debug!(
            "{} rewards in {}/{}",
            rows.len(),
            &ancestor_info.consensus_hash,
            &ancestor_info.anchored_header.block_hash()
        );
        Ok(rows)
    }
```
