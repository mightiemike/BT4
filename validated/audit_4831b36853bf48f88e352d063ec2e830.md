### No vulnerability found for this question.

**Rationale:** The claimed divergence requires two nodes to derive different `block_tenure_burn_height` values for the *same* block. But `block_tenure_burn_height` is not derived from an in-flux "current tip" snapshot — it comes from `validate_nakamoto_tenure_snapshot`, which looks up `tenure_burn_chain_tip` via `SortitionDB::get_block_snapshot_consensus(db_handle, &block.header.consensus_hash)` [1](#0-0) . This is a deterministic, permanent 1:1 mapping from the block's own `consensus_hash` to the sortition snapshot (and its `block_height`) that produced it, and it also asserts canonicity against the sortition tip [2](#0-1) . That value is passed unchanged into `validate_nakamoto_block_static`, which then looks up the epoch at that height [3](#0-2) .

If a peer hasn't yet processed the relevant sortition, `get_block_snapshot_consensus` returns `None` and `validate_nakamoto_tenure_snapshot` fails with `InvalidStacksBlock("No sortition for block's consensus hash")` rather than falling back to some other/stale burn height [4](#0-3) . This is exactly the documented "block stored before parent processed" case, and it fails safe (deferred, re-validated later) instead of producing a wrong-but-consistent-looking result [5](#0-4) .

Since `consensus_hash → sortition snapshot → block_height` is a fixed, globally-agreed mapping once the sortition is processed (and gossip-timing only determines *whether* validation can proceed yet, not *what* height it computes), there is no code path in which two honest nodes, both having processed the tenure's sortition, would derive different `cur_epoch_id` for the same block. The equality `expected_version_for_epoch(epoch derived on A) == expected_version_for_epoch(epoch derived on B)` therefore always holds for any block that both nodes actually validate to completion.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2619-2624)
```rust
    ///
    /// This function will return Ok(None) if the given block's parent is not yet processed.  This
    /// by itself is not necessarily an error, because a block can be stored for subsequent
    /// processing before its parent has been processed.  The `Self::append_block()` function,
    /// however, will flag a block as invalid in this case, because the parent must be available in
    /// order to process a block.
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2661-2673)
```rust
        let consensus_hash = &block.header.consensus_hash;

        let sort_tip = SortitionDB::get_canonical_burn_chain_tip(db_handle)?;

        // burn chain tip that selected this commit's block (the tenure sortition)
        let Some(tenure_burn_chain_tip) =
            SortitionDB::get_block_snapshot_consensus(db_handle, consensus_hash)?
        else {
            warn!("No sortition for {}", consensus_hash);
            return Err(ChainstateError::InvalidStacksBlock(
                "No sortition for block's consensus hash".into(),
            ));
        };
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2676-2696)
```rust
        let Some(ancestor_sort_id) = get_ancestor_sort_id(
            db_handle,
            tenure_burn_chain_tip.block_height,
            &sort_tip.sortition_id,
        )?
        else {
            // not canonical
            warn!("Invalid consensus hash: snapshot is not canonical"; "consensus_hash" => %consensus_hash);
            return Err(ChainstateError::InvalidStacksBlock(
                "No sortition for block's consensus hash -- not canonical".into(),
            ));
        };
        if ancestor_sort_id != tenure_burn_chain_tip.sortition_id {
            // not canonical
            warn!("Invalid consensus hash: snapshot is not canonical"; "consensus_hash" => %consensus_hash);
            return Err(ChainstateError::InvalidStacksBlock(
                "No sortition for block's consensus hash -- not canonical".into(),
            ));
        };

        Ok(tenure_burn_chain_tip)
```

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
