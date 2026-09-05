### Title
`check_first_nakamoto_tenure_change` accepts any canonical-burnchain-ancestor epoch2 block as the first Nakamoto tenure's parent, not just the true canonical epoch2 Stacks tip - (File: `stackslib/src/chainstate/nakamoto/tenure.rs`)

### Summary
`NakamotoChainState::check_first_nakamoto_tenure_change` only verifies that `TenureChangePayload.previous_tenure_end` names a locally-stored, epoch2, single-block-tenure header whose consensus hash matches `prev_tenure_consensus_hash`; canonicity of that consensus hash is checked only against the *burnchain* fork by the caller `check_nakamoto_tenure`, not against the *Stacks-level* canonical epoch2 tip (`SortitionDB::get_canonical_stacks_chain_tip_hash_and_height`). Because epoch2.x fork choice is based on highest work/height with deterministic tie-breaking (not simply "sortition is on the canonical burn fork"), a sortition consensus hash can be canonical on the burnchain while its associated Stacks block is not the actual epoch2 tip. This lets the winner of the very first Nakamoto sortition anchor the Nakamoto chain to a stale epoch2 ancestor instead of the real tip, permanently orphaning honestly-mined epoch2 blocks between the two.

### Finding Description
The invariant that must hold is: **the first Nakamoto tenure's parent block == the canonical epoch2.x Stacks chain tip** at the epoch2→epoch3 boundary, as documented in the module doc comment ("The first-ever Nakamoto tenure's parent block is the last epoch2 Stacks block") [1](#0-0) .

`check_nakamoto_tenure` falls into the first-tenure path whenever `get_ongoing_tenure` on the block's parent returns `None`, delegating entirely to `check_first_nakamoto_tenure_change`: [2](#0-1) 

`check_first_nakamoto_tenure_change` performs these checks only:
1. `cause.expects_sortition()`.
2. `Self::get_block_header(headers_conn, &tenure_payload.previous_tenure_end)` exists in the *local* chainstate headers table.
3. `previous_tenure_blocks == 1`.
4. `prev_tenure_consensus_hash == parent_header.consensus_hash`.
5. The header is an epoch2 header. [3](#0-2) 

It never queries `SortitionDB::get_canonical_stacks_chain_tip_hash_and_height` or otherwise confirms that `parent_header` is the *actual* epoch2 tip. The only canonicity check applied to `prev_tenure_consensus_hash` happens earlier in the caller, `check_valid_consensus_hash`, which only confirms the consensus hash's sortition is on the canonical **burnchain** fork and is height-ordered before the tenure/sortition snapshots — it says nothing about Stacks-level fork choice: [4](#0-3) [5](#0-4) 

The codebase itself acknowledges elsewhere that "sortition is canonical on burn fork" is only a *proxy* for epoch2 Stacks-block canonicity, and even then only for use in non-consensus code, using a distinct function that explicitly re-derives the canonical epoch2 tip via `SortitionDB`'s `canonical_stacks_tip_*` fields and tie-breaking logic: [6](#0-5) 
That real epoch2 tip-selection logic (`update_new_block_arrivals`/`break_canonical_stacks_tip_tie`) is driven by height/work and deterministic hash tie-breaks, not merely "is the consensus hash canonical": [7](#0-6) [8](#0-7) 

**Exploit flow**: The single miner who wins the first post-activation (Epoch 3.0) sortition controls `tenure_consensus_hash` and is free to choose `previous_tenure_end` to point at *any* epoch2 block whose consensus hash is an ancestor on the canonical burnchain fork — not necessarily the true epoch2 tip (`canonical_stacks_tip_hash`/`canonical_stacks_tip_consensus_hash`). Because `check_nakamoto_tenure`/`check_first_nakamoto_tenure_change` is a pure, deterministic function of local chainstate.db and sortition-DB burnchain canonicity (not of the node's tracked "canonical Stacks tip"), every honest node that has synced the same epoch2 history to that point will accept this block identically. This is not a node-disagreement bug so much as a specification/validation gap: the accepted first-tenure parent can be a stale epoch2 fork block, silently and permanently orphaning any epoch2 blocks mined between that stale ancestor and the true tip — including their coinbase/fee rewards, which can never mature since no Nakamoto descendant chain will ever build on them again (epoch2 blocks are inert after the Nakamoto boundary).

Existing guards checked and found insufficient for this specific case:
- `check_valid_consensus_hash` — validates burnchain-fork canonicity only, not Stacks-fork canonicity.
- `previous_tenure_blocks != 1` guard — only sanity-checks tenure length, not tip identity.
- `get_ongoing_tenure` — correctly returns `None` (that's what routes into the vulnerable path); it does not perform the missing check itself.

### Impact Explanation
If exploited, the true epoch2 tip and every valid epoch2 block mined after the attacker's chosen ancestor are permanently orphaned once the Nakamoto chain roots itself elsewhere — an irreversible reorg at the epoch2→epoch3 boundary. Miners of the orphaned epoch2 blocks lose already-mined block rewards/fees that can no longer mature (since the pointer that would have credited them is severed). This matches the Critical category: "block-reward theft/double-payment/loss, permanent freezing via irreversible reorg."

### Likelihood Explanation
The precondition is that the attacker (or any miner, honest or not) wins the single winning sortition immediately at the Epoch 3.0 activation height on the canonical Bitcoin fork — a normal minority-stake/single-miner-slot event, not a majority-stake or Sybil attack. No majority signer or admin privilege is required; the attacker only needs to craft the `TenureChangePayload.previous_tenure_end` field of their own tenure-change transaction to reference an earlier (but still canonical-burnchain) epoch2 consensus hash instead of the true tip. This is a one-shot, activation-boundary-only opportunity (repeatable only across networks/testnets each time an epoch2→epoch3 transition occurs), but fully within reach of a single miner slot.

### Recommendation
In `check_first_nakamoto_tenure_change` (or its caller before dispatching to it), explicitly compare `parent_header`'s `(consensus_hash, block_hash)` against the sortition DB's canonical epoch2 Stacks tip via `SortitionDB::get_canonical_stacks_chain_tip_hash_and_height` (or equivalent `canonical_stacks_tip_consensus_hash`/`canonical_stacks_tip_hash` snapshot fields) evaluated at the burnchain view relevant to the first Nakamoto sortition, and reject the tenure-change if `previous_tenure_end` does not match the true canonical epoch2 tip.

### Proof of Concept
Rust integration test plan (two-fork harness on a local chainstate):
1. Build an epoch2.x chain to the Nakamoto activation height, then fork it into two epoch2 Stacks histories `F1` (ending at block `B_tip`, height H) and `F2` (an earlier ancestor `B_anc`, height H-k), both of whose consensus hashes are canonical on the same (single) canonical Bitcoin fork (simulate via a Stacks-level fork where a miner intentionally builds on an older parent, then apply `update_new_block_arrivals`/`break_canonical_stacks_tip_tie` to establish `B_tip` as the actual `canonical_stacks_tip_hash`).
2. Assert precondition equality: `SortitionDB::get_canonical_stacks_chain_tip_hash_and_height() == (CH_tip, B_tip, H)` and `B_anc != B_tip`.
3. Mine the first post-activation Nakamoto sortition; construct a `TenureChangePayload` with `previous_tenure_end = StacksBlockId(CH_anc, B_anc)` (the stale ancestor) instead of `StacksBlockId(CH_tip, B_tip)`.
4. Call `NakamotoChainState::check_nakamoto_tenure` (or run full block acceptance) and assert it returns `Ok(None)`/rejects the block, i.e. the equality "first Nakamoto tenure's parent == canonical epoch2 tip" is enforced.
5. Currently expected (bug) result: the check returns `Ok(Some(..))` and accepts the block, proving the broken equality — i.e., `check_first_nakamoto_tenure_change` succeeds even though `B_anc != canonical_stacks_tip_hash`.

### Citations

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L25-27)
```rust
//! The tenures within one burnchain fork are well-ordered.  Each tenure has exactly one parent
//! tenure, such that the last block in the parent tenure is the parent of the first block in the
//! child tenure.  The first-ever Nakamoto tenure's parent block is the last epoch2 Stacks block.
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L553-612)
```rust
    pub(crate) fn check_first_nakamoto_tenure_change(
        headers_conn: &Connection,
        tenure_payload: &TenureChangePayload,
    ) -> Result<Option<NakamotoTenureEvent>, ChainstateError> {
        // must be a tenure-change
        if !tenure_payload.cause.expects_sortition() {
            warn!("Invalid tenure-change: not a sortition-induced tenure-change";
                  "consensus_hash" => %tenure_payload.tenure_consensus_hash,
                  "previous_tenure_end" => %tenure_payload.previous_tenure_end
            );
            return Ok(None);
        }

        let Some(parent_header) =
            Self::get_block_header(headers_conn, &tenure_payload.previous_tenure_end)?
        else {
            warn!("Invalid tenure-change from epoch2: no parent epoch2 header";
                  "consensus_hash" => %tenure_payload.tenure_consensus_hash,
                  "previous_tenure_end" => %tenure_payload.previous_tenure_end
            );
            return Ok(None);
        };
        if tenure_payload.previous_tenure_blocks != 1 {
            warn!("Invalid tenure-change from epoch2: expected 1 previous tenure block";
                  "consensus_hash" => %tenure_payload.tenure_consensus_hash,
                  "previous_tenure_blocks" => %tenure_payload.previous_tenure_blocks
            );
            return Ok(None);
        }
        if tenure_payload.prev_tenure_consensus_hash != parent_header.consensus_hash {
            warn!("Invalid tenure-change from epoch2: parent tenure consensus hash mismatch";
                  "prev_tenure_consensus_hash" => %tenure_payload.prev_tenure_consensus_hash,
                  "parent_header.consensus_hash" => %parent_header.consensus_hash
            );
            return Ok(None);
        }
        let Some(epoch2_header_info) = parent_header.anchored_header.as_stacks_epoch2() else {
            warn!("Invalid tenure-change: parent header is not epoch2";
                  "consensus_hash" => %tenure_payload.tenure_consensus_hash,
                  "previous_tenure_end" => %tenure_payload.previous_tenure_end
            );
            return Ok(None);
        };

        // synthesize the "last epoch2" tenure info, so we can calculate the first nakamoto tenure
        let last_epoch2_tenure = NakamotoTenureEvent {
            tenure_id_consensus_hash: parent_header.consensus_hash.clone(),
            prev_tenure_id_consensus_hash: ConsensusHash([0x00; 20]), // ignored,
            burn_view_consensus_hash: parent_header.consensus_hash.clone(),
            cause: TenureChangeCause::BlockFound,
            block_hash: epoch2_header_info.block_hash(),
            block_id: StacksBlockId::new(
                &parent_header.consensus_hash,
                &epoch2_header_info.block_hash(),
            ),
            coinbase_height: epoch2_header_info.total_work.work,
            num_blocks_confirmed: 1,
        };
        Ok(Some(last_epoch2_tenure))
    }
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L614-637)
```rust
    /// Check that a consensus hash is on the canonical burnchain fork
    /// Returns Some(corresponding snapshot) if so
    /// Returns None if it's not on the canonical fork
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

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L697-733)
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
                }
                TenureChangeCause::Extended
                | TenureChangeCause::ExtendedRuntime
                | TenureChangeCause::ExtendedReadCount
                | TenureChangeCause::ExtendedReadLength
                | TenureChangeCause::ExtendedWriteCount
                | TenureChangeCause::ExtendedWriteLength => {
                    // prev and current tenure consensus hashes must be identical
                    if prev_sn.consensus_hash != tenure_sn.consensus_hash {
                        warn!("Invalid tenure-change extension: parent snapshot is not the same as the current tenure snapshot"; "tenure_consensus_hash" => %tenure_payload.tenure_consensus_hash, "prev_tenure_consensus_hash" => %tenure_payload.prev_tenure_consensus_hash);
                        return Ok(None);
                    }
                }
            }

            if prev_sn.block_height > sortition_sn.block_height {
                // parent comes after tip
                warn!("Invalid tenure-change: parent snapshot comes after current tip"; "burn_view_consensus_hash" => %tenure_payload.burn_view_consensus_hash, "prev_tenure_consensus_hash" => %tenure_payload.prev_tenure_consensus_hash);
                return Ok(None);
            }
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L761-771)
```rust
        // What tenure are we building off of?  This is the tenure in which the parent block
        // resides.  Note that if this block is a tenure-extend block, then parent_block_id and
        // this block reside in the same tenure (but this block will insert a tenure-extend record
        // into the tenure-changes table).
        let Some(parent_tenure) =
            Self::get_ongoing_tenure(headers_conn, &block_header.parent_block_id)?
        else {
            // not building off of a previous Nakamoto tenure.  This is the first tenure change.  It should point to an epoch
            // 2.x block.
            return Self::check_first_nakamoto_tenure_change(headers_conn.sqlite(), tenure_payload);
        };
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L3229-3246)
```rust
    /// Get the first canonical block header in a vector of height-ordered candidates
    fn get_highest_canonical_block_header_from_candidates(
        sort_db: &SortitionDB,
        candidates: Vec<StacksHeaderInfo>,
    ) -> Result<Option<StacksHeaderInfo>, ChainstateError> {
        let canonical_sortition_handle = sort_db.index_handle_at_tip();
        for candidate in candidates.into_iter() {
            // if burn_view is None, then this is an epoch 2.x header, and since epoch 2.x tenure's correspond
            // to a single stacks block, we can use the miner's tenure sortition as a proxy for canonicity.
            let candidate_ch = candidate
                .burn_view
                .as_ref()
                .unwrap_or(&candidate.consensus_hash);
            let in_canonical_fork = canonical_sortition_handle.processed_block(candidate_ch)?;
            if in_canonical_fork {
                return Ok(Some(candidate));
            }
        }
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L6488-6537)
```rust
    /// Resolve ties between blocks at the same height.
    /// Hashes the given snapshot's sortition hash with the index block hash for each block
    /// (calculated from `new_block_arrivals`' consensus hash and block header hash), and chooses
    /// the block in `new_block_arrivals` whose resulting hash is lexographically the smallest.
    /// Returns the index into `new_block_arrivals` for the block whose hash is the smallest.
    fn break_canonical_stacks_tip_tie(
        tip: &BlockSnapshot,
        best_height: u64,
        new_block_arrivals: &[(ConsensusHash, BlockHeaderHash, u64)],
    ) -> Option<usize> {
        // if there's a tie, then randomly and deterministically pick one
        let mut tied = vec![];
        for (i, (consensus_hash, block_bhh, height)) in new_block_arrivals.iter().enumerate() {
            if best_height == *height {
                tied.push((StacksBlockId::new(consensus_hash, block_bhh), i));
            }
        }

        let Some(tied_0) = tied.first() else {
            return None;
        };
        if tied.len() == 1 {
            return Some(tied_0.1);
        }

        // break ties by hashing the index block hash with the snapshot's sortition hash, and
        // picking the lexicographically smallest one
        let mut hash_tied = vec![];
        let mut mapping = HashMap::new();
        for (block_id, arrival_idx) in tied.into_iter() {
            let mut buff = [0u8; 64];
            buff[0..32].copy_from_slice(&block_id.0);
            buff[32..64].copy_from_slice(&tip.sortition_hash.0);

            let hashed = Sha512Trunc256Sum::from_data(&buff);
            hash_tied.push(hashed.clone());
            mapping.insert(hashed, arrival_idx);
        }

        hash_tied.sort();
        let winner = hash_tied
            .first()
            .expect("FATAL: zero-length list of tied block IDs");

        let winner_index = *mapping
            .get(winner)
            .expect("FATAL: winning block ID not mapped");

        Some(winner_index)
    }
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L6616-6675)
```rust
        let mut best_tip_block_bhh = parent_tip.canonical_stacks_tip_hash.clone();
        let mut best_tip_consensus_hash = parent_tip.canonical_stacks_tip_consensus_hash.clone();
        let mut best_tip_height = parent_tip.canonical_stacks_tip_height;
        let mut ret = vec![];

        debug!(
            "Current best tip is {}/{} (height {})",
            &best_tip_consensus_hash, &best_tip_block_bhh, best_tip_height
        );

        for (consensus_hash, block_bhh, height) in new_block_arrivals.iter() {
            ret.push((block_bhh.clone(), *height));

            // genesis blocks are incomparable -- it doesn't matter which one was "first."
            // everyone else must be higher than the highest known tip to supersede it.
            if *height > best_tip_height || (*height == 0 && best_tip_height == 0) {
                debug!(
                    "At tip {}: {}/{} (height {}) is superceded by {}/{} (height {})",
                    &parent_tip.burn_header_hash,
                    &best_tip_consensus_hash,
                    &best_tip_block_bhh,
                    best_tip_height,
                    consensus_hash,
                    block_bhh,
                    *height
                );

                best_tip_block_bhh = block_bhh.clone();
                best_tip_consensus_hash = consensus_hash.clone();
                best_tip_height = *height;
            }
        }

        // if there's a tie, then randomly and deterministically pick one
        let winning_index_opt = SortitionHandleTx::break_canonical_stacks_tip_tie(
            parent_tip,
            best_tip_height,
            &new_block_arrivals,
        );
        if let Some(winning_index) = winning_index_opt {
            let winner = new_block_arrivals.get(winning_index).ok_or_else(|| {
                error!("Failed to index the winner of a stacks tip tie");
                db_error::Corruption
            })?;
            best_tip_consensus_hash = winner.0.clone();
            best_tip_block_bhh = winner.1.clone();
        }

        debug!(
            "Max arrival for child of {best_tip_consensus_hash} is {max_arrival_index} (hash {best_tip_block_bhh} height {best_tip_height})"
        );

        Ok((
            best_tip_consensus_hash,
            best_tip_block_bhh,
            best_tip_height,
            max_arrival_index,
            ret,
        ))
    }
```
