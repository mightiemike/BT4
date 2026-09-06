[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2703-2733)
```rust
    fn validate_nakamoto_block_static(
        mainnet: bool,
        chain_id: u32,
        sortdb_conn: &Connection,
        block: &NakamotoBlock,
        block_tenure_burn_height: u64,
    ) -> Result<(), ChainstateError> {
        // Look up the epoch at this tenure's burn height. A Nakamoto block is
        // only ever valid in epoch 3.0 or later, so clamp the result up to
        // epoch 3.0. This is needed to handle the 2.5 -> 3.0 transition.
        let cur_epoch = SortitionDB::get_stacks_epoch(sortdb_conn, block_tenure_burn_height)?
            .expect("FATAL: no epoch defined for current Stacks block");
        let cur_epoch_id = cur_epoch.epoch_id.max(StacksEpochId::Epoch30);

        // static checks on the header and transactions all pass
        let valid = block.validate_header_static(cur_epoch_id)
            && block.validate_transactions_static(mainnet, chain_id, cur_epoch_id);
        if !valid {
            warn!(
                "Invalid Nakamoto block, failed static checks: {}/{} (epoch {})",
                &block.header.consensus_hash,
                &block.header.block_hash(),
                cur_epoch_id
            );
            return Err(ChainstateError::InvalidStacksBlock(
                "Invalid Nakamoto block: failed static checks".into(),
            ));
        }

        Ok(())
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2739-2764)
```rust
    pub(crate) fn validate_normal_nakamoto_block_burnchain(
        staging_db: NakamotoStagingBlocksConnRef,
        db_handle: &SortitionHandleConn,
        expected_burn: Option<u64>,
        block: &NakamotoBlock,
        mainnet: bool,
        chain_id: u32,
    ) -> Result<(), ChainstateError> {
        assert!(!block.is_shadow_block());

        let tenure_burn_chain_tip = Self::validate_nakamoto_tenure_snapshot(db_handle, block)?;

        // block-commit of this sortition
        let Some(block_commit) = db_handle.get_block_commit_by_txid(
            &tenure_burn_chain_tip.sortition_id,
            &tenure_burn_chain_tip.winning_block_txid,
        )?
        else {
            warn!(
                "No block commit for {} in sortition for {}",
                &tenure_burn_chain_tip.winning_block_txid, &block.header.consensus_hash
            );
            return Err(ChainstateError::InvalidStacksBlock(
                "No block-commit in sortition for block's consensus hash".into(),
            ));
        };
```
