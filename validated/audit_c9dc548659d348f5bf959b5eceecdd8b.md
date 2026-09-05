No vulnerability found for this question.

`get_block_commit_parent_sortition_id` is a pure, deterministic SQLite lookup against the `block_commit_parents` table, keyed by `(txid, sortition_id)` [1](#0-0) . The row it reads is populated once, at block-commit insertion time, from `parent_sortition_id` computed by looking up the snapshot at `block_commit.parent_block_ptr` — a burnchain height taken directly from the block-commit itself [2](#0-1) . Both `txid` and `sortition_id` are canonical identifiers already agreed upon by the burnchain consensus (Bitcoin) and the sortition history, not values subject to "arrival order" at the node processing this query. Every node that has processed the same burnchain up to that sortition will compute the identical `parent_block_ptr` lookup and thus store/retrieve the identical `parent_sortition_id`.

Callers such as `NakamotoChainState::get_parent_vrf_proof` use this strictly to walk backward from a known, already-fixed `(consensus_hash, block_commit_txid)` to find the parent sortition for VRF-proof retrieval [3](#0-2) , and `SortitionDB::descends_from`-style ancestry checks use it purely as a memoization/fast-path over an already-defined ancestor chain, falling back to `get_block_commit_by_txid` + `parent_block_ptr` if the memo is missing [4](#0-3) . Nothing about this function influences fork-choice/canonical-tip selection itself — it doesn't compare burn totals, VRF outcomes, or signer weights, and it has no dependency on message arrival order at the local node. An attacker who broadcasts a block-commit only controls `parent_block_ptr`/`parent_vtxindex` fields that are already subject to Bitcoin-consensus ordering and validated elsewhere (e.g., `get_block_commit_parent`, sortition winner selection) before this memoization table is even populated [5](#0-4) .

Since the equality "canonical tip picked by node A == canonical tip picked by node B" does not depend on this lookup's timing or on any attacker-supplied non-determinism, there is no reachable path from an unprivileged attacker's block-commit, leader-key, Nakamoto block/microblock, poison report, or fork extension that causes two honest nodes to diverge through this specific function.

### Citations

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L1179-1208)
```rust
            // step back to the parent
            match SortitionDB::get_block_commit_parent_sortition_id(
                self.sqlite(),
                &sn.winning_block_txid,
                &sn.sortition_id,
            )? {
                Some(parent_sortition_id) => {
                    // we have the block_commit parent memoization data
                    test_debug!(
                        "Parent sortition of {} memoized as {}",
                        &sn.winning_block_txid,
                        &parent_sortition_id
                    );
                    sn = SortitionDB::get_block_snapshot(self.sqlite(), &parent_sortition_id)?
                        .ok_or_else(|| db_error::NotFoundError)?;
                }
                None => {
                    // we do not have the block_commit parent memoization data
                    // step back to the parent
                    test_debug!("No parent sortition memo for {}", &sn.winning_block_txid);
                    let block_commit = get_block_commit_by_txid(
                        self.sqlite(),
                        &sn.sortition_id,
                        &sn.winning_block_txid,
                    )?
                    .expect("CORRUPTION: winning block commit for snapshot not found");
                    sn = self
                        .get_block_snapshot_by_height(block_commit.parent_block_ptr as u64)?
                        .ok_or_else(|| db_error::NotFoundError)?;
                }
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L3131-3139)
```rust
    pub fn get_block_commit_parent_sortition_id(
        conn: &Connection,
        txid: &Txid,
        sortition_id: &SortitionId,
    ) -> Result<Option<SortitionId>, db_error> {
        let qry = "SELECT parent_sortition_id AS sortition_id FROM block_commit_parents WHERE block_commit_parents.block_commit_txid = ?1 AND block_commit_parents.block_commit_sortition_id = ?2";
        let args = params![txid, sortition_id];
        query_row(conn, qry, args)
    }
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L5261-5279)
```rust
    /// Get a parent block commit at a specific location in the burn chain on a particular fork.
    /// Returns None if there is no block commit at this location.
    pub fn get_block_commit_parent<C: SortitionContext>(
        ic: &IndexDBConn<'_, C, SortitionId>,
        block_height: u64,
        vtxindex: u32,
        tip: &SortitionId,
    ) -> Result<Option<LeaderBlockCommitOp>, db_error> {
        if block_height >= BLOCK_HEIGHT_MAX {
            return Err(db_error::BlockHeightOutOfRange);
        }
        let ancestor_id = match get_ancestor_sort_id(ic, block_height, tip)? {
            Some(id) => id,
            None => {
                return Ok(None);
            }
        };

        SortitionDB::get_block_commit_of_sortition(ic, &ancestor_id, block_height, vtxindex)
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L5931-5942)
```rust
        // find parent block commit's snapshot's sortition ID.
        // If the parent_block_ptr doesn't point to a valid snapshot, then store an empty
        // sortition.  If we're not testing, then this should never happen.
        let parent_sortition_id = self
            .get_block_snapshot_by_height(block_commit.parent_block_ptr as u64)?
            .map(|parent_commit_sn| parent_commit_sn.sortition_id)
            .unwrap_or(SortitionId([0x00; 32]));

        if !cfg!(test) && (block_commit.parent_block_ptr != 0 || block_commit.parent_vtxindex != 0)
        {
            assert!(parent_sortition_id != SortitionId([0x00; 32]));
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L3496-3513)
```rust
        let sn = SortitionDB::get_block_snapshot_consensus(sortdb_conn, consensus_hash)?.ok_or(
            ChainstateError::InvalidStacksBlock("No sortition for consensus hash".into()),
        )?;

        let parent_sortition_id = SortitionDB::get_block_commit_parent_sortition_id(
            sortdb_conn,
            block_commit_txid,
            &sn.sortition_id,
        )?
        .ok_or(ChainstateError::InvalidStacksBlock(
            "Parent block-commit is not in this block's sortition history".into(),
        ))?;

        let parent_sn = SortitionDB::get_block_snapshot(sortdb_conn, &parent_sortition_id)?.ok_or(
            ChainstateError::InvalidStacksBlock(
                "Parent block-commit does not have a sortition".into(),
            ),
        )?;
```
