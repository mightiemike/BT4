### Title
`validate_shadow_parent_burnchain` fails open when the parent shadow block header is missing locally, causing a validation-verdict divergence between nodes that have/haven't stored the shadow tenure - ([File: stackslib/src/chainstate/nakamoto/shadow.rs])

### Summary
`NakamotoChainState::validate_shadow_parent_burnchain` returns `Ok(())` immediately if `staging_db.get_nakamoto_block_header(&block.header.parent_block_id)` returns `None`, treating "parent header not found locally" identically to "parent is not a shadow block." A node that has not yet stored the referenced shadow tenure will skip the vtxindex/block-ptr consistency check entirely, while a node that has the shadow block staged will enforce it, producing different accept/reject verdicts for the identical malformed block.

### Finding Description
The documented invariant is: if a normal block's parent is a shadow block, the block-commit's `parent_vtxindex` must be `0` and `parent_block_ptr` must equal the shadow tenure's sortition height [1](#0-0) .

The implementation is:
```rust
let Some(parent_header) =
    staging_db.get_nakamoto_block_header(&block.header.parent_block_id)?
else {
    return Ok(());
};

if !parent_header.is_shadow_block() {
    return Ok(());
}
``` [2](#0-1) 

The broken equality is: `verdict(Node A, block)` where Node A has the referenced shadow block's header already present in its `nakamoto_staging_blocks` table, versus `verdict(Node B, block)` where Node B does not yet have that header stored. On Node A, `get_nakamoto_block_header` returns `Some(parent_header)`, `parent_header.is_shadow_block()` is `true`, and the function proceeds to check `block_commit.parent_vtxindex != 0` and `block_commit.parent_block_ptr` against the shadow tenure's sortition height [3](#0-2) . A malformed block-commit is rejected with `ChainstateError::InvalidStacksBlock`. On Node B, the lookup returns `None` and the function unconditionally returns `Ok(())`, so the same malformed block is *not* rejected by this check — it is accepted or rejected based purely on whatever other checks run afterward in `validate_normal_nakamoto_block_burnchain`, none of which are shown to independently re-verify shadow-parent-specific commit-pointer semantics.

Root cause: the function conflates two semantically distinct conditions — "parent header unknown to this node" and "parent is confirmed not a shadow block" — into a single fail-open early return. This is a genuine logic defect: absence of local knowledge is treated as proof of a negative.

### Impact Explanation
If Node A and Node B disagree on this specific block because one has ingested the shadow tenure into its `nakamoto_staging_blocks` table and the other has not, they will diverge on whether to accept the crafted block, causing tip disagreement precisely at the shadow-tenure boundary the shadow-block mechanism was designed to repair — the exact "Critical: invalid block accepted network-wide vs. valid block rejected" scenario. In practice, however, shadow blocks are only inserted via a coordinated node-software schema update tied to a specific SIP; every node running the correct release adds the same shadow blocks at startup (`add_shadow_block`/`process_shadow_block`), and `add_shadow_block` fails if a non-shadow tenure already occupies that consensus hash [4](#0-3) . So the divergence window is bounded to nodes still running pre-upgrade software or that have not yet completed the schema migration/backfill for that specific shadow tenure — not an indefinitely repeatable attacker-controlled state.

### Likelihood Explanation
Exploiting this requires: (1) a known shadow tenure exists in canonical history (defined by an emergency SIP, not attacker-controlled), (2) some fraction of the network has not yet locally stored that shadow block's header (a real but transient condition during a coordinated upgrade rollout), and (3) the attacker crafts a normal block whose `parent_block_id` points at the shadow block and whose block-commit has a mismatched `parent_vtxindex`/`parent_block_ptr`. The attacker needs only a single miner slot/block-commit — no majority stake is required to attempt submission — but successful exploitation depends on the target population still lacking the shadow header, which is an operational/rollout condition rather than something the attacker can force at will. This bounds likelihood to the upgrade-transition window following a shadow-block SIP deployment.

### Recommendation
Distinguish "parent header not found" from "parent confirmed non-shadow." When the parent header cannot be located, the function should not silently return `Ok(())`; it should either (a) fail closed by returning an error/deferring processing until the parent is known, or (b) explicitly check whether the referenced tenure is known to be a shadow tenure via `is_shadow_tenure`/`get_shadow_tenure_start_block` using the tenure's consensus hash (available independent of having downloaded the shadow block itself) so the validation isn't purely conditioned on local storage state. At minimum, block processing should require that the parent block be fully staged/known before this check executes, closing the gap where "unknown parent" is treated as "verified non-shadow parent."

### Proof of Concept
```rust
// stackslib/src/chainstate/nakamoto/tests/shadow_parent_divergence.rs (new)
//
// Two-node harness:
// 1. Both nodes advance to a common tenure boundary where a shadow tenure S
//    is defined in canonical burnchain history (consensus_hash CH_S).
// 2. Node A: call `tx.add_shadow_block(&shadow_block_S)` so
//    `staging_db.get_nakamoto_block_header(&shadow_block_S.block_id())`
//    returns `Some(..)`.
// 3. Node B: do NOT insert shadow_block_S into its staging DB
//    (simulating a node that hasn't applied the shadow-block schema update yet).
// 4. Craft a normal Nakamoto block `child` with:
//    - `child.header.parent_block_id == shadow_block_S.block_id()`
//    - an accompanying `LeaderBlockCommitOp` with `parent_vtxindex = 7`
//      (non-zero, violating the invariant) and `parent_block_ptr` NOT equal
//      to `parent_sn.block_height` of the shadow tenure's sortition.
// 5. Call `NakamotoChainState::validate_shadow_parent_burnchain(staging_db, db_handle, &child, &block_commit)`
//    on both Node A and Node B's staging_db/db_handle.
//
// Assertions:
// assert!(matches!(
//     result_on_node_a,
//     Err(ChainstateError::InvalidStacksBlock(_))
// )); // Node A rejects: parent_header found, is_shadow_block() true, vtxindex check fails
//
// assert!(result_on_node_b.is_ok()); // Node B fails open: get_nakamoto_block_header returns None
//
// // The equality `verdict(Node A) == verdict(Node B)` is violated:
// assert_ne!(result_on_node_a.is_ok(), result_on_node_b.is_ok());
```

### Citations

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L206-216)
```rust
    /// Verify that the shadow parent of a normal block is consistent with the normal block's
    /// tenure's block-commit.
    ///
    /// * the block-commit vtxindex must be 0 (i.e. burnchain coinbase)
    /// * the block-commit block ptr must be the shadow parent tenure's sortition
    ///
    /// Returns Ok(()) if the parent is _not_ a shadow block
    /// Returns Ok(()) if the parent is a shadow block, and the above criteria are met
    /// Returns Err(ChainstateError::InvalidStacksBlock(..)) if the parent is a shadow block, and
    /// some of the criteria above are false
    /// Returns Err(..) on other (DB-related) errors
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L223-233)
```rust
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

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L836-848)
```rust
        // this tenure must be empty, or it must be a shadow tenure
        let qry = "SELECT 1 FROM nakamoto_staging_blocks WHERE consensus_hash = ?1";
        let args = rusqlite::params![&shadow_block.header.consensus_hash];
        let present: Option<u32> = query_row(self, qry, args)?;
        if present.is_some()
            && !self
                .conn()
                .is_shadow_tenure(&shadow_block.header.consensus_hash)?
        {
            return Err(ChainstateError::InvalidStacksBlock(
                "Shadow block cannot be inserted into non-empty non-shadow tenure".into(),
            ));
        }
```
