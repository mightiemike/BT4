No vulnerability found for this question.

**Analysis summary:**

The premise doesn't hold up against the actual code path. `get_parent_matured_miner` does NOT re-invoke `get_header_by_coinbase_height` to locate the parent — it reads the parent identity directly from the fields stored on the matured miner's own `MinerPaymentSchedule` record (`miner.parent_consensus_hash`, `miner.parent_block_hash`), which were set once at block-processing time in `calculate_scheduled_tenure_reward`. [1](#0-0) 

Those parent fields were derived from that specific block's own `parent_block_id` chain when the block was originally processed, not from a second independent coinbase-height lookup that could diverge across forks: [2](#0-1) 

`coinbase_height` is a fork-scoped MARF key (`nakamoto_keys::ongoing_tenure_coinbase_height`) that only increments when a tenure actually exists (i.e., there was a winning sortition); missed sortitions never occupy a coinbase-height slot, so there is no "shift" that causes one fork's coinbase_height N to alias a *different* tenure than in another fork — the mapping is written once per tenure and is fork-consistent by construction. [3](#0-2) [4](#0-3) 

Finally, at the storage layer, `try_add_parent`'s two rows are only ever fetched together via an exact `parent_index_block_hash AND child_index_block_hash` match, and both rows are inserted together from the same `reward_info` struct that was computed for one specific child/parent pair: [5](#0-4) [6](#0-5) 

Because parent-lookup never depends on a second, independently-computed `coinbase_height` resolution, there is no mechanism by which a missed-sortition-induced height shift could cause `try_add_parent` to merge a child from one tenure with a parent's `tx_fees_streamed_produced` from an unrelated tenure. The claimed equality violation does not occur.

### Citations

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L4694-4722)
```rust
    pub fn get_parent_matured_miner(
        conn: &DBConn,
        mainnet: bool,
        latest_matured_miners: &[MinerPaymentSchedule],
    ) -> Result<MinerPaymentSchedule, Error> {
        let parent_miner = if let Some(miner) = latest_matured_miners.first().as_ref() {
            StacksChainState::get_scheduled_block_rewards_at_block(
                conn,
                &StacksBlockHeader::make_index_block_hash(
                    &miner.parent_consensus_hash,
                    &miner.parent_block_hash,
                ),
            )?
            .pop()
            .unwrap_or_else(|| {
                if miner.parent_consensus_hash == FIRST_BURNCHAIN_CONSENSUS_HASH
                    && miner.parent_block_hash == FIRST_STACKS_BLOCK_HASH
                {
                    MinerPaymentSchedule::genesis(mainnet)
                } else {
                    panic!(
                        "CORRUPTION: parent {}/{} of {}/{} not found in DB",
                        &miner.parent_consensus_hash,
                        &miner.parent_block_hash,
                        &miner.consensus_hash,
                        &miner.block_hash
                    );
                }
            })
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L965-1017)
```rust
        let coinbase_at_block = StacksChainState::get_coinbase_reward(
            evaluated_epoch,
            chainstate_tx.config.mainnet,
            chain_tip_burn_header_height,
            burn_dbconn.context.first_block_height,
        );

        let total_coinbase = coinbase_at_block.saturating_add(accumulated_rewards);
        let parent_tenure_start_header: StacksHeaderInfo = Self::get_header_by_coinbase_height(
            chainstate_tx.deref_mut(),
            &block.header.parent_block_id,
            parent_coinbase_height,
        )?
        .ok_or_else(|| {
            warn!("While processing tenure change, failed to look up parent tenure";
                  "parent_coinbase_height" => parent_coinbase_height,
                  "parent_block_id" => %block.header.parent_block_id,
                  "consensus_hash" => %block.header.consensus_hash,
                  "stacks_block_hash" => %block.header.block_hash(),
                  "stacks_block_id" => %block.header.block_id()
            );
            ChainstateError::NoSuchBlockError
        })?;
        // fetch the parent tenure fees by reading the total tx fees from this block's
        // *parent* (not parent_tenure_start_header), because `parent_block_id` is the last
        // block of that tenure, so contains a total fee accumulation for the whole tenure
        let parent_tenure_fees = if parent_tenure_start_header.is_nakamoto_block() {
            Self::get_total_tenure_tx_fees_at(
                chainstate_tx,
                &block.header.parent_block_id
            )?.ok_or_else(|| {
                warn!("While processing tenure change, failed to look up parent block's total tx fees";
                      "parent_block_id" => %block.header.parent_block_id,
                      "consensus_hash" => %block.header.consensus_hash,
                      "stacks_block_hash" => %block.header.block_hash(),
                      "stacks_block_id" => %block.header.block_id()
                    );
                ChainstateError::NoSuchBlockError
            })?
        } else {
            // if the parent tenure is an epoch-2 block, don't pay
            // any fees to them in this schedule: nakamoto blocks
            // cannot confirm microblock transactions, and
            // anchored transactions are scheduled
            // by the parent in epoch-2.
            0
        };

        Ok(Self::make_scheduled_miner_reward(
            mainnet,
            evaluated_epoch,
            &parent_tenure_start_header.anchored_header.block_hash(),
            &parent_tenure_start_header.consensus_hash,
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2991-3023)
```rust
    pub fn get_header_by_coinbase_height<SDBI: StacksDBIndexed>(
        conn: &mut SDBI,
        tip_index_hash: &StacksBlockId,
        coinbase_height: u64,
    ) -> Result<Option<StacksHeaderInfo>, ChainstateError> {
        // nakamoto block?
        if let Some(block_id) =
            conn.get_nakamoto_block_id_at_coinbase_height(tip_index_hash, coinbase_height)?
        {
            return Self::get_block_header_nakamoto(conn.sqlite(), &block_id);
        }

        // epoch2 block?
        let Some(ancestor_at_height) = conn
            .get_ancestor_block_id(coinbase_height, tip_index_hash)?
            .map(|ancestor| Self::get_block_header(conn.sqlite(), &ancestor))
            .transpose()?
            .flatten()
        else {
            warn!("No such epoch2 ancestor";
                  "coinbase_height" => coinbase_height,
                  "tip_index_hash" => %tip_index_hash,
            );
            return Ok(None);
        };
        // only return if it is an epoch-2 block, because that's
        // the only case where block_height can be interpreted as
        // tenure height.
        if ancestor_at_height.is_epoch_2_block() {
            return Ok(Some(ancestor_at_height));
        }

        Ok(None)
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L3611-3622)
```rust
    /// `tip` is the block at which the MARF lookup will be done; it must be `block` itself or a
    /// descendant of it on the same fork. The coinbase-height mapping is written once per tenure
    /// and never changes, so any such tip yields the same value. Pass the canonical tip to keep
    /// the read off blocks a squashed snapshot may have pruned.
    pub fn get_coinbase_height_at_tip<SDBI: StacksDBIndexed>(
        chainstate_conn: &mut SDBI,
        block: &StacksBlockId,
        tip: &StacksBlockId,
    ) -> Result<Option<u64>, ChainstateError> {
        // nakamoto header?
        if let Some(hdr) = Self::get_block_header_nakamoto(chainstate_conn.sqlite(), block)? {
            return Ok(chainstate_conn.get_coinbase_height(tip, &hdr.consensus_hash)?);
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L641-650)
```rust
    fn inner_get_matured_miner_payments(
        conn: &DBConn,
        parent_block_id: &TenureBlockId,
        child_block_id: &TenureBlockId,
    ) -> Result<Vec<MinerReward>, Error> {
        let sql = "SELECT * FROM matured_rewards WHERE parent_index_block_hash = ?1 AND child_index_block_hash = ?2 AND vtxindex = 0";
        let args = params![parent_block_id.0, child_block_id.0];
        let ret: Vec<MinerReward> = query_rows(conn, sql, args).map_err(Error::DBError)?;
        Ok(ret)
    }
```

**File:** stackslib/src/chainstate/stacks/db/mod.rs (L2911-2941)
```rust
        if let Some((miner_payout, user_payouts, parent_payout, reward_info)) = mature_miner_payouts
        {
            let rewarded_miner_block_id = StacksBlockHeader::make_index_block_hash(
                &reward_info.from_block_consensus_hash,
                &reward_info.from_stacks_block_hash,
            );
            let rewarded_parent_miner_block_id = StacksBlockHeader::make_index_block_hash(
                &reward_info.from_parent_block_consensus_hash,
                &reward_info.from_parent_stacks_block_hash,
            );

            StacksChainState::insert_matured_child_miner_reward(
                headers_tx.deref_mut(),
                &rewarded_parent_miner_block_id,
                &rewarded_miner_block_id,
                &miner_payout,
            )?;
            for user_payout in user_payouts.into_iter() {
                StacksChainState::insert_matured_child_user_reward(
                    headers_tx.deref_mut(),
                    &rewarded_parent_miner_block_id,
                    &rewarded_miner_block_id,
                    &user_payout,
                )?;
            }
            StacksChainState::insert_matured_parent_miner_reward(
                headers_tx.deref_mut(),
                &rewarded_parent_miner_block_id,
                &rewarded_miner_block_id,
                &parent_payout,
            )?;
```
