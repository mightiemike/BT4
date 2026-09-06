### Title
Shadow-parent block-commit `parent_vtxindex` validation is skipped when the parent header hasn't yet been fetched, causing a lagging node to accept a block a synced node rejects - (File: `stackslib/src/chainstate/nakamoto/shadow.rs`)

### Summary
`NakamotoChainState::validate_shadow_parent_burnchain` only enforces `block_commit.parent_vtxindex == 0` and `parent_block_ptr == parent_sn.block_height` when the parent header is already present in `staging_db`; if the parent header (a shadow block) has not yet been downloaded/stored, it silently returns `Ok(())`. This check runs exactly once, at `accept_block` time, and is never repeated when the block is later dequeued for processing in `process_next_ready_nakamoto_block`, so a node that races ahead of its peer in fetching the shadow parent will reject a malformed block while a lagging node will permanently accept and process it.

### Finding Description
The broken equality is:

`validate_shadow_parent_burnchain(node_with_parent_header_stored, block, commit)` != `validate_shadow_parent_burnchain(node_without_parent_header_stored, block, commit)`

when the true parent is in fact a shadow block and `block_commit.parent_vtxindex != 0`.

Code path:
- `NakamotoChainState::validate_shadow_parent_burnchain` looks up the parent header via `staging_db.get_nakamoto_block_header(&block.header.parent_block_id)`; if `None`, it returns `Ok(())` unconditionally: [1](#0-0) 
- If the parent header *is* present and is a shadow block, a nonzero `block_commit.parent_vtxindex` is rejected: [2](#0-1) 
- This function is invoked from `validate_normal_nakamoto_block_burnchain`, which is itself invoked only once, from `NakamotoChainState::accept_block`, at the moment the block is received off the wire: [3](#0-2) [4](#0-3) 
- When the block is later dequeued for real state processing in `process_next_ready_nakamoto_block`, only `parent_block_id` hash continuity is checked; `validate_shadow_parent_burnchain`/`validate_normal_nakamoto_block_burnchain` is never re-invoked. The code even explicitly documents this assumption: "This should be checked already during block acceptance and parent block processing... the check during block acceptance makes sure that the staging db doesn't get into a situation where it continuously tries to retry such a block:" [5](#0-4) 

Exploit flow: an attacker creates a valid shadow tenure (or waits for one), then broadcasts a Bitcoin block-commit for a child tenure whose `parent_vtxindex != 0` (violating the required linkage to a shadow parent) along with a signed Nakamoto child block referencing that shadow block as parent. A node that has already fetched/stored the shadow parent header will find it in `staging_db` and reject the child at `accept_block` (the block is never even queued). A node that has not yet fetched the shadow parent header (e.g., one lagging in download, or one that receives the child block via P2P before the shadow-parent block itself) will pass `validate_shadow_parent_burnchain` with `Ok(())`, and the block will be queued in the staging DB. Once that node later downloads the shadow parent and processes it, `process_next_ready_nakamoto_block` will attach and process the previously-queued, block-commit-invalid child without re-running the burnchain-linkage check, permanently advancing that node's chain tip past a block the other node never accepted.

The test suite in `stackslib/src/chainstate/nakamoto/tests/node.rs` explicitly documents the `Ok(())`-on-missing-parent behavior as intentional only for the "parent is an epoch2 block not present in staging chainstate" case, not for the "parent is a not-yet-fetched shadow block" case — the function cannot distinguish the two, which is the root cause: [6](#0-5) 

### Impact Explanation
This is a Critical, network-wide consensus divergence: an invalid block (one whose block-commit fails the mandatory shadow-parent linkage rule) is permanently accepted and applied by one honest node while being outright rejected by another honest node that happened to fetch the shadow-parent header first. The two nodes end up with different canonical Stacks tips with no reconciliation mechanism, since the accepting node's staging-DB record for the bad block is never revisited/revalidated. This matches the "invalid block accepted on a lagging node but rejected on a synced node" Critical category exactly.

### Likelihood Explanation
The only precondition is a legitimate shadow-block tenure existing on-chain (shadow blocks are an emergency-SIP mechanism but once a shadow tenure exists this class of attack is repeatable against it), and an attacker able to broadcast a Bitcoin block-commit and a signed Nakamoto block, which is exactly the unprivileged capability granted in scope. The attacker needs no majority stake — only ordinary miner-slot/BTC-fee cost to place one block-commit — and can win the race deterministically by targeting nodes that are known to be behind on chainstate sync (e.g., new nodes, nodes recovering from downtime, or nodes still catching up after an outage), or simply by exploiting normal network propagation-order variance between "shadow parent block" and "child block" delivery.

### Recommendation
`validate_shadow_parent_burnchain` must not return `Ok(())` merely because the parent header is absent from `staging_db`. Instead:
1. Re-run `validate_normal_nakamoto_block_burnchain` (or at least `validate_shadow_parent_burnchain`) at `process_next_ready_nakamoto_block` time, once the parent header becomes available, before allowing the block to be applied.
2. Alternatively, when the parent header is missing at `accept_block` time, defer acceptance (return a "not yet processable" state rather than `Ok(())`) instead of skipping the check outright, and only finalize the accept decision once the parent's shadow/non-shadow status is definitively known.

### Proof of Concept
Rust integration test plan (two-node harness):
1. Set up two nodes, A and B, both following the same burnchain/sortition history, with a shadow tenure `S` already present with a known start block header `S_start`.
2. On node A, first deliver and store `S_start` into `staging_db` (simulating a synced node). On node B, do not deliver `S_start` yet (simulating a lagging node).
3. Construct a normal Nakamoto child block `C` with `header.parent_block_id = S_start.block_id()`, and a `LeaderBlockCommitOp` for `C`'s tenure with `parent_vtxindex = 1` (invalid; must be 0 for a shadow parent) and `parent_block_ptr` matching `S`'s sortition height.
4. Call `NakamotoChainState::accept_block` for `C` on node A: assert it returns `Err(ChainstateError::InvalidStacksBlock(_))` and `C` is never present in `staging_db`.
5. Call `NakamotoChainState::accept_block` for `C` on node B: assert it returns `Ok(true)` (stored to staging), because `staging_db.get_nakamoto_block_header(&S_start.block_id())` returns `None` on B.
6. Now deliver `S_start` to node B (`add_shadow_block`/`process_shadow_block`), then call `NakamotoChainState::process_next_ready_nakamoto_block` (or the coordinator's block-processing loop) on node B repeatedly.
7. Assert that node B's canonical Nakamoto tip advances to include `C` (`NakamotoChainState::get_canonical_block_header` returns `C`'s header), while node A's canonical tip remains at `S_start`/its predecessor and never advances to `C` even after being offered the same block-commit and block bytes — demonstrating the tip divergence between the two "honest" nodes for the identical input set.

### Citations

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L217-233)
```rust
    pub(crate) fn validate_shadow_parent_burnchain(
        staging_db: NakamotoStagingBlocksConnRef,
        db_handle: &SortitionHandleConn,
        block: &NakamotoBlock,
        block_commit: &LeaderBlockCommitOp,
    ) -> Result<(), ChainstateError> {
        // only applies if the parent is a nakamoto block (since all shadow blocks are nakamoto
        // blocks)
        let Some(parent_header) =
            staging_db.get_nakamoto_block_header(&block.header.parent_block_id)?
        else {
            return Ok(());
        };

        if !parent_header.is_shadow_block() {
            return Ok(());
        }
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L235-253)
```rust
        if block_commit.parent_vtxindex != 0 {
            warn!("Invalid Nakamoto block: parent {} of {} is a shadow block but block-commit vtxindex is {}", &parent_header.block_id(), &block.block_id(), block_commit.parent_vtxindex);
            return Err(ChainstateError::InvalidStacksBlock("Invalid Nakamoto block: invalid block-commit parent vtxindex for parent shadow block".into()));
        }
        let Some(parent_sn) =
            SortitionDB::get_block_snapshot_consensus(db_handle, &parent_header.consensus_hash)?
        else {
            warn!(
                "Invalid Nakamoto block: No sortition for parent shadow block {}",
                &block.header.parent_block_id
            );
            return Err(ChainstateError::InvalidStacksBlock(
                "Invalid Nakamoto block: parent shadow block has no sortition".into(),
            ));
        };
        if u64::from(block_commit.parent_block_ptr) != parent_sn.block_height {
            warn!("Invalid Nakamoto block: parent {} of {} is a shadow block but block-commit parent ptr is {}", &parent_header.block_id(), &block.block_id(), block_commit.parent_block_ptr);
            return Err(ChainstateError::InvalidStacksBlock("Invalid Nakamoto block: invalid block-commit parent block ptr for parent shadow block".into()));
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2357-2410)
```rust
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

        // sanity check -- must attach to parent
        let parent_block_id = StacksBlockId::new(
            &parent_header_info.consensus_hash,
            &parent_header_info.anchored_header.block_hash(),
        );
        if parent_block_id != next_ready_block.header.parent_block_id {
            drop(chainstate_tx);

            let msg = "Discontinuous Nakamoto Stacks block";
            warn!("{}", &msg;
                  "child parent_block_id" => %next_ready_block.header.parent_block_id,
                  "expected parent_block_id" => %parent_block_id,
                  "consensus_hash" => %next_ready_block.header.consensus_hash,
                  "stacks_block_hash" => %next_ready_block.header.block_hash(),
                  "stacks_block_id" => %next_ready_block.header.block_id()
            );
            let staging_block_tx = stacks_chain_state.staging_db_tx_begin()?;
            staging_block_tx.set_block_orphaned(&block_id)?;
            staging_block_tx.commit()?;
            return Err(ChainstateError::InvalidStacksBlock(msg.into()));
        }

        // set the sortition handle's pointer to the block's burnchain view.
        //   this is either:
        //    (1)  set by the tenure change tx if one exists
        //    (2)  the same as parent block id
        let burnchain_view =
            Self::get_block_burn_view(sort_db, &next_ready_block, &parent_header_info)?;
        let Some(burnchain_view_sn) =
            SortitionDB::get_block_snapshot_consensus(sort_db.conn(), &burnchain_view)?
        else {
            // This should be checked already during block acceptance and parent block processing
            //   - The check for expected burns returns `NoSuchBlockError` if the burnchain view
            //      could not be found for a block with a tenure tx.
            // We error here anyways, but the check during block acceptance makes sure that the staging
            //  db doesn't get into a situation where it continuously tries to retry such a block (because
            //  such a block shouldn't land in the staging db).
            warn!(
                "Cannot process Nakamoto block: failed to find Sortition ID associated with burnchain view";
                "consensus_hash" => %next_ready_block.header.consensus_hash,
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2766-2771)
```rust
        // if the *parent* of this block is a shadow block, then the block-commit's
        // parent_vtxindex *MUST* be 0 and the parent_block_ptr *MUST* be the tenure of the
        // shadow block.
        //
        // if the parent is not a shadow block, then this is a no-op.
        Self::validate_shadow_parent_burnchain(staging_db, db_handle, block, &block_commit)?;
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2881-2896)
```rust
        // this block must be consistent with its miner's leader-key and block-commit, and must
        // contain only transactions that are valid in this epoch.
        Self::validate_normal_nakamoto_block_burnchain(
            staging_db_tx.conn(),
            db_handle,
            expected_burn_opt,
            block,
            config.mainnet,
            config.chain_id,
        )
        .inspect_err(|e| {
            warn!("Unacceptable Nakamoto block; will not store";
                "stacks_block_id" => %block_id,
                "error" => ?e
            );
        })?;
```

**File:** stackslib/src/chainstate/nakamoto/tests/node.rs (L2373-2381)
```rust
            // not a problem if there's no (nakamoto) parent, since the parent can be a
            // (non-shadow) epoch2 block not present in the staging chainstate
            NakamotoChainState::validate_shadow_parent_burnchain(
                chainstate.nakamoto_blocks_db(),
                &sortdb.index_handle_at_tip(),
                &bad_block_no_parent,
                &tenure_block_commit,
            )
            .unwrap();
```
