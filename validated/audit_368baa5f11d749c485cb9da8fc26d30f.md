### Title
Missing deterministic sibling tie-break in Nakamoto block processing causes memoized canonical-tip divergence between nodes - ([File: stackslib/src/chainstate/nakamoto/staging_blocks.rs])

### Summary
`store_block_if_better` only rejects a competing block when it shares the *same* `block_hash` as an already-stored block; two genuinely distinct sibling blocks (different content/hash) at the same `chain_length`/parent are both inserted into `nakamoto_staging_blocks` unconditionally. `next_ready_nakamoto_block` then selects the next block to process with no deterministic tiebreak among same-height siblings, and `process_next_nakamoto_block` processes and unconditionally memoizes ("records new arrival") *every* ready block it dequeues, including both siblings, one after another. The block processed **last** wins the memoized canonical-tip pointer, and "last" is determined purely by arrival/insertion order into each node's local SQLite staging table — which differs between nodes that receive the two siblings in reversed order.

### Finding Description
The broken equality is `SortitionDB::get_canonical_stacks_chain_tip_hash(node A) == SortitionDB::get_canonical_stacks_chain_tip_hash(node B)` (used, e.g., in `get_stacks_chain_tips`) [1](#0-0) .

Path:
1. `NakamotoChainState::accept_block` verifies signer signatures and, for each valid block, calls `store_block_if_better` [2](#0-1) .
2. `store_block_if_better` first tries `try_store_block_with_new_signer_sighash`, which only checks for an existing row with the *same* `consensus_hash`+`block_hash`. Two distinct sibling blocks (different content, hence different hash) both pass this "new sighash" branch and are stored as separate rows — the signing-weight comparison logic (`existing_signing_weight < signing_weight`) is only reached for identical-hash replacements, never applied between two different sibling blocks [3](#0-2) .
3. `next_ready_nakamoto_block` selects the next block to process via a query that only orders `ORDER BY child.height ASC`, with **no tiebreak** (not by signing weight, not by hash) among multiple children sharing the same height/parent [4](#0-3) . SQLite resolves ties in an implementation-defined order that tracks row/insertion order — i.e., arrival order.
4. `handle_new_nakamoto_stacks_block` loops, calling `process_next_nakamoto_block` repeatedly "at most one block per loop pass" until `Ok(None)` [5](#0-4) . Because processing sibling #1 does **not** orphan sibling #2 (they are not parent/child, they are true siblings), sibling #2 still satisfies the ready-block predicate (`parent.processed=1 AND child.processed=0 AND child.orphaned=0 AND child.burn_attachable=1`) and gets processed too, in the very next loop iteration.
5. Each time a block is processed, `process_next_nakamoto_block` unconditionally calls `sort_tx.set_stacks_block_accepted(...)` — described in the code itself as "record new arrival" — which overwrites the memoized canonical Stacks tip fields with whatever block was just processed, with **no comparison of signing weight or any fork-choice rule** [6](#0-5) , [7](#0-6) .
6. `SortitionDB::get_canonical_stacks_chain_tip_hash_and_height` reads back whichever tip was last memoized this way via `get_canonical_nakamoto_tip_hash_and_height_and_burn_view`, which is a straight `ORDER BY block_height DESC LIMIT 1` lookup keyed on the last write, not a re-derivation of "most signing weight" [8](#0-7) , [9](#0-8) .

Because step 3's tiebreak is arrival-order-dependent and step 6's memoization is last-write-wins, two nodes that receive sibling blocks X and Y in opposite order will each process X and Y but in the *opposite sequence*, causing each node's memoized tip to end up pointing at the *other* node's discarded sibling. `verify_signer_signatures` and `check_tenure_tx` correctly validate each sibling individually (that guard is about validity, not about which competing valid sibling is canonical), so they do not prevent this divergence; there is no equivalent of the signing-weight comparison from `store_block_if_better`'s same-hash-replace path applied at the fork-choice/tip-memoization layer.

### Impact Explanation
Two honest, fully-synced nodes seeing the identical burnchain state and the identical set of two valid, sufficiently-signed sibling blocks can end up with permanently different `get_canonical_stacks_chain_tip_hash` results — a chain split at the fork-choice/query layer. This affects every consumer of the canonical tip (RPC responses, mining/tenure logic, event dispatch), and is repeatable any time equally-valid siblings race into different nodes in different orders. It requires no majority stake — only the ability to get two independently-signed sibling blocks (both crossing the signer threshold, as could occur with double-proposals/network partitions among signers) delivered to different nodes in different orders, which matches "Critical - a chain split... temporary tip disagreement" per the scoped severity model.

### Likelihood Explanation
Preconditions are exactly those enumerated in the question: same reward cycle/canonical sortition tip, two distinct blocks at the same `chain_length`/parent each carrying valid signer signatures at or above threshold. No majority signer or node-operator privilege is required — the attacker (or even honest concurrent miner/signer behavior under network partition) only needs to cause propagation-order differences, which is trivial for an unprivileged network participant relaying blocks to different peers in different order. This is realistically triggerable and repeatable per race window, without any BTC cost beyond what's already required to win the tenure/produce a valid signed block.

### Recommendation
Make canonical-tip selection deterministic and independent of arrival order:
1. Add a deterministic tiebreak to `next_ready_nakamoto_block`'s ready-block query (e.g., `ORDER BY child.height ASC, child.signing_weight DESC, child.block_hash ASC`), so all nodes process same-height siblings in the same order.
2. Do not let every processed sibling unconditionally overwrite the memoized canonical tip in `set_stacks_block_accepted`; instead compare against the previously memoized value at that height (e.g., prefer higher `signing_weight`, and only break further ties with a fixed, hash-based rule) rather than "last write wins."
3. Consider orphaning/rejecting lower-signing-weight siblings once a higher-signing-weight sibling at the same height/parent has been chosen, so the staging DB and processing loop stop re-processing genuinely conflicting siblings as if they were both canonical.

### Proof of Concept
Rust integration test plan (two-node/two-fork harness):
1. Build two `TestPeer`s (`node_a`, `node_b`) booted to the same Nakamoto tip via `boot_into_nakamoto_peers`, sharing the same sortition/burnchain state.
2. Construct two valid sibling `NakamotoBlock`s `block_x` and `block_y` with identical `parent_block_id`/`consensus_hash`/`chain_length`, each independently signed by signers so that `verify_signer_signatures` succeeds for both (mirroring `stacks-signer/src/v0/tests.rs::run_sibling_scenario`'s sibling construction) [10](#0-9) .
3. On `node_a`: call `NakamotoChainState::accept_block` for `block_x` then `block_y` (in that order), then drive `handle_new_nakamoto_stacks_block`/`process_next_nakamoto_block` to exhaustion.
4. On `node_b`: call `accept_block` for `block_y` then `block_x` (reversed order), then likewise drain the processing loop.
5. Assert:
   ```rust
   let tip_a = SortitionDB::get_canonical_stacks_chain_tip_hash(node_a.sortdb().conn()).unwrap();
   let tip_b = SortitionDB::get_canonical_stacks_chain_tip_hash(node_b.sortdb().conn()).unwrap();
   assert_eq!(tip_a, tip_b, "Nodes must agree on the canonical Stacks chain tip");
   ```
   Expect this assertion to **fail** on the current code (`tip_a` == block_id(block_y), `tip_b` == block_id(block_x)), demonstrating the divergence.

### Citations

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L6495-6506)
```rust
    pub fn get_stacks_chain_tips(&self, sortdb: &SortitionDB) -> Result<Vec<StagingBlock>, Error> {
        let (consensus_hash, block_bhh) =
            SortitionDB::get_canonical_stacks_chain_tip_hash(sortdb.conn())?;
        let sql = "SELECT * FROM staging_blocks WHERE processed = 1 AND orphaned = 0 AND consensus_hash = ?1 AND anchored_block_hash = ?2";
        let args = params![consensus_hash, block_bhh];
        let Some(staging_block): Option<StagingBlock> =
            query_row(self.db(), sql, args).map_err(Error::DBError)?
        else {
            return Ok(vec![]);
        };
        self.get_stacks_chain_tips_at_height(staging_block.height)
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2520-2547)
```rust
        // as a separate transaction, mark this block as processed.
        // This is done separately so that the staging blocks DB, which receives writes
        // from the network to store blocks, will be available for writes while a block is
        // being processed. Therefore, it's *very important* that block-processing happens
        // within the same, single thread.  Also, it's *very important* that this update
        // succeeds, since *we have already processed* the block.
        Self::infallible_set_block_processed(stacks_chain_state, &block_id);

        let signer_bitvec = (next_ready_block).header.pox_treatment.clone();

        let block_timestamp = next_ready_block.header.timestamp;

        // set stacks block accepted
        let mut sort_tx = sort_db.tx_handle_begin(canonical_sortition_tip)?;
        sort_tx.set_stacks_block_accepted(
            &next_ready_block.header.consensus_hash,
            &burnchain_view,
            &next_ready_block.header.block_hash(),
            next_ready_block.header.chain_length,
        )?;

        sort_tx
            .commit()
            .unwrap_or_else(|e| {
                error!("Failed to commit sortition db transaction after committing chainstate and clarity block. The chainstate database is now corrupted.";
                       "error" => ?e);
                panic!()
            });
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2917-2937)
```rust
        let signing_weight = block
            .header
            .verify_signer_signatures(reward_set, epoch_id)
            .inspect_err(|e| {
                warn!("Received block, but the signer signatures are invalid";
                    "block_id" => %block_id,
                    "error" => ?e,
                );
            })?;

        // if we pass all the tests, then along the way, we will have verified (in
        // Self::validate_nakamoto_block_burnchain) that the consensus hash of this block is on the
        // same sortition history as `db_handle` (and thus it must be burn_attachable)
        let burn_attachable = true;

        let ret = staging_db_tx.store_block_if_better(
            block,
            burn_attachable,
            signing_weight,
            obtain_method,
        )?;
```

**File:** stackslib/src/chainstate/nakamoto/staging_blocks.rs (L466-476)
```rust
    pub(crate) fn next_ready_nakamoto_block(
        &self,
        header_conn: &Connection,
    ) -> Result<Option<(NakamotoBlock, u64)>, ChainstateError> {
        let query = "SELECT child.data FROM nakamoto_staging_blocks child JOIN nakamoto_staging_blocks parent
                     ON child.parent_block_id = parent.index_block_hash
                     WHERE child.burn_attachable = 1
                       AND child.orphaned = 0
                       AND child.processed = 0
                       AND parent.processed = 1
                     ORDER BY child.height ASC";
```

**File:** stackslib/src/chainstate/nakamoto/staging_blocks.rs (L603-663)
```rust
    pub fn store_block_if_better(
        &self,
        block: &NakamotoBlock,
        burn_attachable: bool,
        signing_weight: u32,
        obtain_method: NakamotoBlockObtainMethod,
    ) -> Result<bool, ChainstateError> {
        let block_id = block.block_id();
        let block_hash = block.header.block_hash();
        let consensus_hash = block.header.consensus_hash.clone();

        // case 1 -- no block with this sighash exists.
        if self.try_store_block_with_new_signer_sighash(
            block,
            burn_attachable,
            signing_weight,
            obtain_method,
        )? {
            debug!("Stored block with new sighash";
                   "block_id" => %block_id,
                   "block_hash" => %block_hash);
            return Ok(true);
        }

        // case 2 -- the block exists. Consider replacing it, but only if its
        // signing weight is higher.
        let (existing_block_id, _processed, orphaned, existing_signing_weight) = self.conn().get_block_processed_and_signed_weight(&consensus_hash, &block_hash)?
            .ok_or_else(|| {
                // this should be unreachable -- there's no record of this block
                error!("Could not store block {block_id} ({consensus_hash}) with block hash {block_hash} -- no record of its processed status or signing weight!");
                ChainstateError::NoSuchBlockError
            })?;

        if orphaned {
            // nothing to do
            debug!("Will not store alternative copy of block {block_id} ({consensus_hash}) with block hash {block_hash}, since a block with the same block hash was orphaned");
            return Ok(false);
        }

        let ret = if existing_signing_weight < signing_weight {
            self.replace_block(block, signing_weight, obtain_method)?;
            debug!("Replaced block";
                   "existing_block_id" => %existing_block_id,
                   "block_id" => %block_id,
                   "block_hash" => %block_hash,
                   "existing_signing_weight" => existing_signing_weight,
                   "signing_weight" => signing_weight);
            true
        } else {
            if existing_signing_weight > signing_weight {
                debug!("Will not store alternative copy of block {block_id} ({consensus_hash}) with block hash {block_hash}, since it has less signing power");
            } else {
                debug!(
                    "Will not store duplicate copy of block {block_id} ({consensus_hash}) with block hash {block_hash}"
                );
            }
            false
        };

        Ok(ret)
    }
```

**File:** stackslib/src/chainstate/nakamoto/coordinator/mod.rs (L861-900)
```rust
        loop {
            Self::fault_injection_pause_nakamoto_block_processing();

            // process at most one block per loop pass
            let mut processed_block_receipt = match NakamotoChainState::process_next_nakamoto_block(
                &mut self.chain_state_db,
                &mut self.sortition_db,
                &canonical_sortition_tip,
                self.dispatcher,
                self.config.txindex,
            ) {
                Ok(receipt_opt) => receipt_opt,
                Err(ChainstateError::InvalidStacksBlock(msg)) => {
                    warn!("Encountered invalid block: {}", &msg);

                    // try again
                    self.notifier.notify_stacks_block_processed();
                    increment_stx_blocks_processed_counter();
                    continue;
                }
                Err(ChainstateError::NetError(NetError::DeserializeError(msg))) => {
                    // happens if we load a zero-sized block (i.e. an invalid block)
                    warn!("Encountered invalid block (codec error): {}", &msg);

                    // try again
                    self.notifier.notify_stacks_block_processed();
                    increment_stx_blocks_processed_counter();
                    continue;
                }
                Err(e) => {
                    // something else happened
                    return Err(e.into());
                }
            };

            let Some(block_receipt) = processed_block_receipt.take() else {
                // out of blocks
                debug!("No more blocks to process (no receipts)");
                break;
            };
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L1634-1665)
```rust
impl SortitionHandleTx<'_> {
    pub fn set_stacks_block_accepted(
        &mut self,
        consensus_hash: &ConsensusHash,
        burn_view_consensus_hash: &ConsensusHash,
        stacks_block_hash: &BlockHeaderHash,
        stacks_block_height: u64,
    ) -> Result<(), db_error> {
        let chain_tip = SortitionDB::get_block_snapshot(self, &self.context.chain_tip)?.expect(
            "FAIL: Setting stacks block accepted in canonical chain tip which cannot be found",
        );

        // record new arrival
        self.set_stacks_block_accepted_at_tip(
            &chain_tip,
            consensus_hash,
            burn_view_consensus_hash,
            stacks_block_hash,
            stacks_block_height,
        )?;

        if cfg!(test) {
            let (ch, bhh) = SortitionDB::get_canonical_stacks_chain_tip_hash(self).unwrap();
            debug!(
                "Memoized canonical Stacks chain tip is now {}/{}, written to {}",
                &ch, &bhh, &self.context.chain_tip
            );
        }

        Ok(())
    }

```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L4799-4830)
```rust
    pub fn get_canonical_nakamoto_tip_hash_and_height_and_burn_view(
        conn: &Connection,
        tip: &BlockSnapshot,
    ) -> Result<Option<(ConsensusHash, ConsensusHash, BlockHeaderHash, u64)>, db_error> {
        // Search stacks_chain_tips_by_burn_view, but give up after a (small) number of rows.
        // This "give up" condition should only be reached when `stacks_chain_tips_by_burn_height`
        // is empty -- i.e. on migration to schema 11.
        let mut cursor = tip.clone();
        for _ in 0..STACKS_TIPS_BY_BURN_VIEW_SEARCH_DEPTH {
            let result_at_tip : Option<(ConsensusHash, ConsensusHash, BlockHeaderHash, u64)> = conn
                .prepare_cached("SELECT consensus_hash,burn_view_consensus_hash, block_hash,block_height FROM stacks_chain_tips_by_burn_view WHERE sortition_id = ? ORDER BY block_height DESC LIMIT 1")?
                .query_row(
                    &[&cursor.sortition_id],
                    |row| Ok((row.get_unwrap(0), row.get_unwrap(1), row.get_unwrap(2), (u64::try_from(row.get_unwrap::<_, i64>(3)).expect("FATAL: block height too high"))))
                ).optional()?;
            test_debug!(
                "Result at tip by burn view ({} {} {}): {:?}",
                &cursor.sortition_id,
                &cursor.consensus_hash,
                cursor.block_height,
                &result_at_tip
            );
            if let Some(stacks_tip) = result_at_tip {
                return Ok(Some(stacks_tip));
            }
            let Some(next_cursor) =
                SortitionDB::get_block_snapshot(conn, &cursor.parent_sortition_id)?
            else {
                return Ok(None);
            };
            cursor = next_cursor
        }
```

**File:** stackslib/src/chainstate/burn/db/sortdb.rs (L4860-4886)
```rust
    /// Get the canonical Stacks chain tip -- this gets memoized on the canonical burn chain tip.
    /// DO NOT CALL during Stacks block processing (including during Clarity VM evaluation). This function returns the latest data known to the node, which may not have been at the time of original block assembly.
    pub fn get_canonical_stacks_chain_tip_hash_and_height(
        conn: &Connection,
    ) -> Result<(ConsensusHash, BlockHeaderHash, u64), db_error> {
        let sn = SortitionDB::get_canonical_burn_chain_tip(conn)?;
        let cur_epoch =
            SortitionDB::get_stacks_epoch(conn, sn.block_height)?.unwrap_or_else(|| {
                panic!(
                    "FATAL: no epoch defined for burn height {}",
                    sn.block_height
                )
            });

        if cur_epoch.epoch_id >= StacksEpochId::Epoch30 {
            // nakamoto behavior -- look to the stacks_chain_tip table
            //  if the chain tip of the current sortition hasn't been set, have to iterate to parent
            return Self::get_canonical_nakamoto_tip_hash_and_height(conn, &sn)?
                .ok_or(db_error::NotFoundError);
        }

        // epoch 2.x behavior -- look at the snapshot itself
        let stacks_block_hash = sn.canonical_stacks_tip_hash;
        let consensus_hash = sn.canonical_stacks_tip_consensus_hash;
        let stacks_block_height = sn.canonical_stacks_tip_height;
        Ok((consensus_hash, stacks_block_hash, stacks_block_height))
    }
```

**File:** stacks-signer/src/v0/tests.rs (L591-640)
```rust
    /// Drive the sibling race: two conflicting tenure-start blocks A and B are both tracked
    /// (as they would be after screening two proposals within the async-validation window),
    /// A's validation returns first and is signed, then B's validation returns. Returns the
    /// resulting `BlockInfo` for A (captured right after its own validation), for B (captured
    /// right after its validation), and for B after an optional re-proposal.
    ///
    /// `tenure_last_block_proposal_timeout` controls whether A's signature is still fresh when
    /// B crosses the pre-commit threshold. `serve_sibling_as_tip` controls whether the mock
    /// node reports A (height 10) or the parent (height 9) as the canonical tenure tip, which
    /// is what the signer consults once the signature has timed out. If `re_propose_b_after` is
    /// set, the miner re-submits B's proposal after that delay (as it does after a signature
    /// timeout) and B's `BlockInfo` is captured again as the third element.
    fn run_sibling_scenario(
        tenure_last_block_proposal_timeout: Duration,
        serve_sibling_as_tip: bool,
        re_propose_b_after: Option<Duration>,
    ) -> (BlockInfo, BlockInfo, Option<BlockInfo>) {
        let miner = StacksPrivateKey::from_seed(&[0, 1]);
        let tenure = ConsensusHash([1; 20]);
        let parent_tenure = ConsensusHash([0; 20]);

        // The parent block of the tenure (height 9); both siblings build on it at height 10.
        let mut parent_header = NakamotoBlockHeader {
            version: 1,
            chain_length: 9,
            burn_spent: 10,
            consensus_hash: parent_tenure.clone(),
            parent_block_id: StacksBlockId([9; 32]),
            tx_merkle_root: Sha512Trunc256Sum([0; 32]),
            state_index_root: TrieHash([0; 32]),
            timestamp: 9,
            miner_signature: MessageSignature::empty(),
            signer_signature: vec![],
            pox_treatment: BitVec::ones(1).unwrap(),
            problematic_txs: vec![],
        };
        parent_header.sign_miner(&miner).unwrap();
        let parent_id = parent_header.block_id();

        // Two conflicting sibling tenure-start blocks: same tenure, parent, and height; the only
        // difference is the timestamp (hence the hash). The timestamps are current so that a
        // re-proposal of B passes the proposal age check.
        let now = get_epoch_time_secs();
        let block_a = tenure_start(&miner, &tenure, &parent_tenure, &parent_id, now);
        let block_b = tenure_start(&miner, &tenure, &parent_tenure, &parent_id, now + 1);
        let hash_a = block_a.header.signer_signature_hash();
        let hash_b = block_b.header.signer_signature_hash();
        assert_ne!(hash_a, hash_b);
        assert_eq!(block_a.header.consensus_hash, block_b.header.consensus_hash);
        assert_eq!(block_a.header.chain_length, block_b.header.chain_length);
```
