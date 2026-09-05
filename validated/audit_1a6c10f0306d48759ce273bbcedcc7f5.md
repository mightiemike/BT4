## No vulnerability found for this question.

The claimed exploit path is architecturally accounted for and does not achieve any Critical/High impact.

**Equality traced:** `block.header.burn_spent == expected_burn` where `expected_burn` comes from `NakamotoChainState::get_expected_burns` [1](#0-0) .

**What actually happens on `accept_block`:** `get_expected_burns` returns `Ok(None)` when the parent header isn't found yet, and this `None` is explicitly allowed to flow into `validate_normal_nakamoto_block_burnchain` -> `validate_normal_against_burnchain` -> `common_validate_against_burnchain`, which skips the burn-total equality check when `expected_burn` is `None` — this is a documented, intentional behavior (see doc comment: "This function will return Ok(None) if the given block's parent is not yet processed. This by itself is not necessarily an error, because a block can be stored for subsequent processing before its parent has been processed. The `Self::append_block()` function, however, will flag a block as invalid in this case...") [2](#0-1) [3](#0-2) .

**Why this can't cause Critical impact:** `accept_block` only inserts the block into the *staging* database; it does not execute the block, update the chain tip, mature rewards, or write to the MARF. Staging is a passive queue — actual state mutation only happens in `append_block`/block processing, which is gated by `find_next_staging_block`/`next_ready_nakamoto_block` requiring the parent to already be processed [4](#0-3) . When the parent later becomes available and the block is actually processed, `get_expected_burns` is recomputed deterministically from the now-available parent header and re-enforced via `common_validate_against_burnchain` (or the direct check at mod.rs:5163) with `Err(ChainstateError::InvalidStacksBlock(...))` on mismatch [5](#0-4) . This is exactly the re-check the question describes, and it does reject the block before any reward maturation, tip update, or MARF write occurs, since these happen only within `append_block`'s block-processing transaction after this check passes.

**Result:** every node, regardless of arrival order, will reach the *same final verdict* once it has the parent: the block is invalid and never becomes part of any processed chain state. The transient staging-only "acceptance" by fast nodes has no observable effect on tip, rewards, or MARF root, and it does not propagate as a *block* (only unvalidated block data sitting in a local staging table) — it is not itself relayed as an accepted/valid block to peers in a way that causes a fork or wrongful accept, since acceptance into staging by one node doesn't force other nodes to treat it as canonical or mature any reward. This matches the intended design noted directly in the code comments, and no chain split, non-reproducible state root, wrongful accept, or reward loss results.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1881-1895)
```rust
        // this block must commit to all of the work seen so far
        if let Some(expected_burn) = expected_burn {
            if self.header.burn_spent != expected_burn {
                warn!("Invalid Nakamoto block header: invalid total burns";
                    "header.burn_spent" => self.header.burn_spent,
                    "expected_burn" => expected_burn,
                    "consensus_hash" => %self.header.consensus_hash,
                    "stacks_block_hash" => %self.header.block_hash(),
                    "stacks_block_id" => %self.header.block_id()
                );
                return Err(ChainstateError::InvalidStacksBlock(
                    "Invalid Nakamoto block: invalid total burns".into(),
                ));
            }
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2355-2369)
```rust
        let (mut chainstate_tx, clarity_instance) = stacks_chain_state.chainstate_tx_begin();

        // find parent header
        let Some(parent_header_info) =
            Self::get_block_header(&chainstate_tx.tx, &next_ready_block.header.parent_block_id)?
        else {
            // no parent; cannot process yet
            debug!("Cannot process Nakamoto block: missing parent header";
                   "consensus_hash" => %next_ready_block.header.consensus_hash,
                   "stacks_block_hash" => %next_ready_block.header.block_hash(),
                   "stacks_block_id" => %next_ready_block.header.block_id(),
                   "parent_block_id" => %next_ready_block.header.parent_block_id
            );
            return Ok(None);
        };
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2620-2624)
```rust
    /// This function will return Ok(None) if the given block's parent is not yet processed.  This
    /// by itself is not necessarily an error, because a block can be stored for subsequent
    /// processing before its parent has been processed.  The `Self::append_block()` function,
    /// however, will flag a block as invalid in this case, because the parent must be available in
    /// order to process a block.
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2625-2648)
```rust
    pub(crate) fn get_expected_burns<SH: SortitionHandle>(
        sort_handle: &SH,
        chainstate_conn: &Connection,
        block: &NakamotoBlock,
    ) -> Result<Option<u64>, ChainstateError> {
        let burn_view_ch = if let Some(tenure_payload) = block.get_tenure_tx_payload() {
            &tenure_payload.burn_view_consensus_hash
        } else {
            // if there's no new tenure for this block, the burn total should be the same as its parent
            let parent_burns_opt =
                Self::get_block_header(chainstate_conn, &block.header.parent_block_id)?
                    .map(|parent| parent.anchored_header.total_burns());
            return Ok(parent_burns_opt);
        };
        let burn_view_sn =
            SortitionDB::get_block_snapshot_consensus(sort_handle.sqlite(), burn_view_ch)?
                .ok_or_else(|| {
                    warn!("Could not load expected burns -- no such burn view";
                          "burn_view_consensus_hash" => %burn_view_ch
                    );
                    ChainstateError::NoSuchBlockError
                })?;
        Ok(Some(burn_view_sn.total_burn))
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L5162-5171)
```rust
        // this block must commit to all of the burnchain spends seen so far
        if block.header.burn_spent != expected_burn {
            warn!("Invalid Nakamoto block header: invalid total burns";
                  "header.burn_spent" => block.header.burn_spent,
                  "expected_burn" => expected_burn,
            );
            return Err(ChainstateError::InvalidStacksBlock(
                "Invalid Nakamoto block: invalid total burns".into(),
            ));
        }
```
