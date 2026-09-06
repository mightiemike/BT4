### No vulnerability found for this question.

The code at [1](#0-0)  already enforces exactly the equality the question describes: it fetches both snapshots via `check_valid_consensus_hash` (which additionally validates each hash is canonical) at [2](#0-1) , then unconditionally rejects (`Ok(None)`) any tenure-change where `tenure_sn.block_height > sortition_sn.block_height`, i.e. where the tenure's snapshot is more recent than the burn view's snapshot. This check runs before any of the branch-specific logic (BlockFound vs Extended causes) and applies to every tenure, not just a subset, so the attacker-crafted out-of-order consensus-hash pair described in the prompt is rejected deterministically by any node evaluating `check_nakamoto_tenure`, regardless of how far along the burnchain that node has synced, since `check_valid_consensus_hash` only returns a snapshot once it is confirmed canonical at the querying node's current view [3](#0-2) .

### Citations

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L617-636)
```rust
    pub(crate) fn check_valid_consensus_hash<SH: SortitionHandle>(
        sort_handle: &mut SH,
        ch: &ConsensusHash,
    ) -> Result<Option<BlockSnapshot>, ChainstateError> {
        // the target sortition must exist, and it must be on the canonical fork
        let Some(sn) = SortitionDB::get_block_snapshot_consensus(sort_handle.sqlite(), ch)? else {
            // no sortition
            warn!("Invalid consensus hash: no such snapshot"; "consensus_hash" => %ch);
            return Ok(None);
        };
        let Some(ancestor_sn) = sort_handle.get_block_snapshot_by_height(sn.block_height)? else {
            // not canonical
            warn!("Invalid consensus hash: snapshot is not canonical"; "consensus_hash" => %ch);
            return Ok(None);
        };
        if ancestor_sn.sortition_id != sn.sortition_id {
            // not canonical
            return Ok(None);
        }
        Ok(Some(sn))
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L678-689)
```rust
        let Some(tenure_sn) =
            Self::check_valid_consensus_hash(sort_handle, &tenure_payload.tenure_consensus_hash)?
        else {
            return Ok(None);
        };
        let Some(sortition_sn) = Self::check_valid_consensus_hash(
            sort_handle,
            &tenure_payload.burn_view_consensus_hash,
        )?
        else {
            return Ok(None);
        };
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L691-695)
```rust
        // tenure_sn must be no more recent than sortition_sn
        if tenure_sn.block_height > sortition_sn.block_height {
            warn!("Invalid tenure-change: tenure snapshot comes before sortition snapshot"; "tenure_consensus_hash" => %tenure_payload.tenure_consensus_hash, "burn_view_consensus_hash" => %tenure_payload.prev_tenure_consensus_hash);
            return Ok(None);
        }
```
