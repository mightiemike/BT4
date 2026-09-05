[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L5094-5097)
```rust
        // look up this block's sortition's burnchain block hash and height.
        // It must exist in the same Bitcoin fork as our `burn_dbconn`.
        let tenure_block_snapshot =
            Self::check_sortition_exists(burn_dbconn, &block.header.consensus_hash)?;
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L4170-4180)
```rust
            db_tx.tx().execute(
                r#"UPDATE snapshots SET
                pox_valid = 0,
                arrival_index = 0,
                canonical_stacks_tip_height = 0,
                canonical_stacks_tip_hash = "0000000000000000000000000000000000000000000000000000000000000000",
                canonical_stacks_tip_consensus_hash = "0000000000000000000000000000000000000000",
                stacks_block_accepted = 0
                WHERE parent_burn_header_hash = ?"#,
                &[&header],
            )?;
```

**File:** stackslib/src/chainstate/coordinator/mod.rs (L946-967)
```rust
/// They may be valid again, after a PoX reorg.
fn forget_orphan_stacks_blocks(
    sort_conn: &DBConn,
    chainstate_db_tx: &mut DBTx,
    burn_header: &BurnchainHeaderHash,
    invalidation_height: u64,
) -> Result<(), Error> {
    if let Ok(sns) = SortitionDB::get_all_snapshots_for_burn_block(sort_conn, burn_header) {
        for sn in sns.into_iter() {
            // only retry blocks that are truly in descendant
            // sortitions.
            if sn.sortition && sn.block_height > invalidation_height {
                StacksChainState::forget_orphaned_epoch_data(
                    chainstate_db_tx,
                    &sn.consensus_hash,
                    &sn.winning_stacks_block_hash,
                )?;
            }
        }
    }
    Ok(())
}
```

**File:** stackslib/src/chainstate/coordinator/mod.rs (L993-1044)
```rust
    fn try_revalidate_sortition(
        &mut self,
        canonical_snapshot: &BlockSnapshot,
        header: &BurnchainBlockHeader,
        last_processed_ancestor: &SortitionId,
        next_pox_info: Option<&RewardCycleInfo>,
    ) -> Result<Option<BlockSnapshot>, Error> {
        let parent_sort_id = self
            .sortition_db
            .get_sortition_id(&header.parent_block_hash, last_processed_ancestor)?
            .ok_or_else(|| {
                warn!("Unknown block {:?}", header.parent_block_hash);
                BurnchainError::MissingParentBlock
            })?;

        let parent_pox = {
            let mut sortition_db_handle =
                SortitionHandleTx::begin(&mut self.sortition_db, &parent_sort_id)?;
            sortition_db_handle.get_pox_id()?
        };

        let new_sortition_id =
            SortitionDB::make_next_sortition_id(parent_pox, &header.block_hash, next_pox_info);
        let sortition_opt =
            SortitionDB::get_block_snapshot(self.sortition_db.conn(), &new_sortition_id)?;

        if let Some(sortition) = sortition_opt {
            // existing sortition -- go revalidate it
            info!(
                "Revalidate already-processed snapshot {new_sortition_id} height {} to have canonical tip {}/{} height {}",
                sortition.block_height,
                &canonical_snapshot.canonical_stacks_tip_consensus_hash,
                &canonical_snapshot.canonical_stacks_tip_hash,
                canonical_snapshot.canonical_stacks_tip_height,
            );

            let tx = self.sortition_db.tx_begin()?;
            SortitionDB::revalidate_snapshot_with_block(
                &tx,
                &new_sortition_id,
                &canonical_snapshot.canonical_stacks_tip_consensus_hash,
                &canonical_snapshot.canonical_stacks_tip_hash,
                canonical_snapshot.canonical_stacks_tip_height,
                Some(false), // we'll mark it processed after this call, if it's still valid.
            )?;
            tx.commit()?;

            Ok(Some(sortition))
        } else {
            Ok(None)
        }
    }
```

**File:** stackslib/src/burnchains/burnchain.rs (L1162-1189)
```rust
    /// Determine if there has been a chain reorg, given our current canonical burnchain tip.
    /// Return the new chain tip and a boolean signaling the presence of a reorg
    fn sync_reorg<I: BurnchainIndexer>(indexer: &mut I) -> Result<(u64, bool), burnchain_error> {
        let headers_path = indexer.get_headers_path();

        // sanity check -- what is the height of our highest header
        let headers_height = indexer
            .get_highest_header_height()
            .inspect_err(|e| error!("Failed to read headers height from {headers_path}: {e:?}"))?;

        if headers_height == 0 {
            return Ok((0, false));
        }

        // did we encounter a reorg since last sync?  Find the highest common ancestor of the
        // remote bitcoin peer's chain state.
        // Note that this value is 0-indexed -- the smallest possible value it returns is 0.
        let reorg_height = indexer
            .find_chain_reorg()
            .inspect_err(|e| error!("Failed to check for reorgs from {headers_path}: {e:?}"))?;

        if reorg_height < headers_height {
            warn!("Burnchain reorg detected: highest common ancestor at height {reorg_height}");
            return Ok((reorg_height, true));
        } else {
            // no reorg
            return Ok((headers_height, false));
        }
```

**File:** stackslib/src/net/p2p.rs (L5315-5343)
```rust
    /// Static helper to check to see if there has been a burnchain reorg
    pub fn is_reorg(
        last_sort_tip: Option<&BlockSnapshot>,
        sort_tip: &BlockSnapshot,
        sortdb: &SortitionDB,
    ) -> bool {
        let Some(last_sort_tip) = last_sort_tip else {
            // no prior tip, so no reorg to handle
            return false;
        };

        if last_sort_tip.block_height == sort_tip.block_height
            && last_sort_tip.consensus_hash == sort_tip.consensus_hash
        {
            // prior tip and current tip are the same, so no reorg
            return false;
        }

        if last_sort_tip.block_height == sort_tip.block_height
            && last_sort_tip.consensus_hash != sort_tip.consensus_hash
        {
            // current and previous sortition tips are at the same height, but represent different
            // blocks.
            info!(
                "Burnchain reorg detected at burn height {}: {} != {}",
                sort_tip.block_height, &last_sort_tip.consensus_hash, &sort_tip.consensus_hash
            );
            return true;
        }
```
