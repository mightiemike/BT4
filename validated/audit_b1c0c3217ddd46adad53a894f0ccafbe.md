### No vulnerability found for this question.

The equality claimed to be at risk — `ancestor_sn.sortition_id == sn.sortition_id` in `check_valid_consensus_hash` — is exactly the guard that correctly rejects non-canonical/losing sortitions, and it functions as intended.

**Trace of the equality:**
`check_valid_consensus_hash` first resolves the attacker-supplied `prev_tenure_consensus_hash` to its `BlockSnapshot` via `SortitionDB::get_block_snapshot_consensus`, which will happily return the snapshot for a losing sortition consensus hash (since consensus hashes are recorded for every sortition, winning or not). It then fetches the *canonical* snapshot at that same `block_height` via `sort_handle.get_block_snapshot_by_height(sn.block_height)` and compares `ancestor_sn.sortition_id` to `sn.sortition_id`. Because `sort_handle` is bound to the canonical/active sortition fork, `get_block_snapshot_by_height` returns the sortition that is actually part of that fork at that height. If the attacker's consensus hash belongs to a losing/non-canonical sortition, its `sortition_id` differs from the canonical one at that height, so the comparison fails and the function returns `Ok(None)`, causing `check_nakamoto_tenure` to reject the tenure at the call sites for `tenure_sn`, `sortition_sn`, and `prev_sn` alike: [1](#0-0) 

This is invoked for `prev_tenure_consensus_hash` specifically inside `check_nakamoto_tenure`: [2](#0-1) 

There is also a second, independent guard: even if a non-canonical consensus hash somehow passed the canonical-fork check, `check_nakamoto_tenure` additionally requires `prev_sn.sortition` to be `true` (i.e., an actual winning sortition, not merely an existing snapshot) unless the parent is a shadow block: [3](#0-2) 

Both guards operate independently and correctly reject a `prev_tenure_consensus_hash` pointing to a losing/non-canonical sortition. The scenario in the question describes the intended, working defense, not a bypass — no equality is broken, and no divergence between honest nodes, reward double-payment, or invalid-block-acceptance path exists here.

### Citations

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L617-637)
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
    }
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L697-713)
```rust
        if tenure_payload.prev_tenure_consensus_hash != FIRST_BURNCHAIN_CONSENSUS_HASH {
            // the parent sortition must exist, must be canonical, and must be an ancestor of the
            // sortition for the given consensus hash.
            let Some(prev_sn) = Self::check_valid_consensus_hash(
                sort_handle,
                &tenure_payload.prev_tenure_consensus_hash,
            )?
            else {
                return Ok(None);
            };
            match tenure_payload.cause {
                TenureChangeCause::BlockFound => {
                    if prev_sn.block_height >= tenure_sn.block_height {
                        // parent comes after child
                        warn!("Invalid tenure-change: parent snapshot comes at or after current tenure"; "tenure_consensus_hash" => %tenure_payload.tenure_consensus_hash, "prev_tenure_consensus_hash" => %tenure_payload.prev_tenure_consensus_hash);
                        return Ok(None);
                    }
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L735-750)
```rust
            // is the parent a shadow block?
            // Only possible if the parent is also a nakamoto block
            let is_parent_shadow_block = NakamotoChainState::get_nakamoto_block_version(
                headers_conn.sqlite(),
                &block_header.parent_block_id,
            )?
            .map(NakamotoBlockHeader::is_shadow_block_version)
            .unwrap_or(false);

            if !is_parent_shadow_block && !prev_sn.sortition {
                // parent wasn't a shadow block (we expect a sortition), but this wasn't a sortition-induced tenure change
                warn!("Invalid tenure-change: no block found";
                      "prev_tenure_consensus_hash" => %tenure_payload.prev_tenure_consensus_hash
                );
                return Ok(None);
            }
```
