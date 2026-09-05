### Title
`validate_shadow_parent_burnchain` silently accepts a bad `block_commit.parent_vtxindex` when the shadow parent header is not yet in `staging_db` - ([File: stackslib/src/chainstate/nakamoto/shadow.rs])

### Summary
`NakamotoChainState::validate_shadow_parent_burnchain` treats "parent header not found in `staging_db`" identically to "parent is not a shadow block," returning `Ok(())` in both cases. Since whether a node has already downloaded/stored the parent Nakamoto header is a function of local sync state rather than of the burnchain/chain state itself, two nodes that differ only in how far they've synced can reach opposite verdicts on the same block.

### Finding Description
The broken equality is:

`validate_shadow_parent_burnchain(node_with_parent_header_stored, block, block_commit) == validate_shadow_parent_burnchain(node_without_parent_header_stored, block, block_commit)`

Tracing `validate_shadow_parent_burnchain` [1](#0-0) :

```
let Some(parent_header) = staging_db.get_nakamoto_block_header(&block.header.parent_block_id)? else {
    return Ok(());
};
if !parent_header.is_shadow_block() {
    return Ok(());
}
if block_commit.parent_vtxindex != 0 { ... return Err(...) }
```

If the local node hasn't yet stored `block.header.parent_block_id`'s header in `staging_db` (e.g. it is still downloading/catching up), the function returns `Ok(())` unconditionally, regardless of whether the true parent (once known) is a shadow block with a bad `block_commit.parent_vtxindex`. A synced node that already has the parent header stored will instead evaluate the `parent_header.is_shadow_block()` branch and correctly reject the block if `block_commit.parent_vtxindex != 0`.

This check is invoked as part of the one-time burnchain-validation gate performed when a block is first received/staged (via the Nakamoto block acceptance path in `mod.rs`, which calls into `shadow.rs`'s burnchain validators). It is a receipt-time check, not something re-evaluated deterministically as a pure function of final chain state — its outcome depends on the *order* in which a given node happened to download blocks. An attacker (a miner with a single slot, or anyone who can submit a block-commit and a following Nakamoto block) can craft a block whose stated parent is a shadow-block tenure, with an incorrect `block_commit.parent_vtxindex` (nonzero), and race the block's delivery to nodes before they have independently fetched/staged the shadow parent's header.

### Impact Explanation
This matches the Critical impact category "an invalid block accepted... but rejected on a synced node" — network-wide, two honest nodes end up in permanent disagreement over whether this block (and its descendant tenure) is valid, since a lagging node stages it as valid (skipping the shadow-vtxindex requirement) while a synced node with the header already present rejects it outright. This produces divergent chain tips/staging state across honest nodes rather than a benign transient sync artifact.

### Likelihood Explanation
The attacker needs no special stake: any actor capable of submitting a Nakamoto block referencing a shadow-tenure parent and a matching Bitcoin block-commit with a bad `parent_vtxindex` can attempt this. The precondition is that the attacker's chosen parent tenure-start block is a shadow block, and that at least one honest node processing the child block has not yet stored that shadow block's header in its local `staging_db` at the moment of validation (a plausible state for any node catching up, restarting, or that received blocks out of order due to normal P2P propagation).

### Recommendation
`validate_shadow_parent_burnchain` should not return `Ok(())` when the parent header cannot be found. Instead, it should either (a) defer/queue validation of the child block until the parent header is available and re-run this exact check at that time before the block can be treated as valid/processed, or (b) explicitly fetch/require the parent header (blocking on it) so that "unknown parent" is not conflated with "non-shadow parent." The check must be a deterministic function of final chain state, not of what the local node happens to have downloaded so far.

### Proof of Concept
Rust two-node integration test plan:
1. Set up a chainstate with a shadow tenure `T_shadow` whose start block header is `H_shadow`, followed by sortition `S_child`.
2. Craft `block_commit` for `S_child` with `parent_vtxindex = 5` (nonzero) and `parent_block_ptr` pointing at `T_shadow`'s sortition height, and a child `NakamotoBlock` `B_child` with `parent_block_id = H_shadow.block_id()`.
3. Node A (`synced`): pre-load `H_shadow` into its `staging_db` via `add_shadow_block`/normal processing, then call `NakamotoChainState::validate_shadow_parent_burnchain(staging_db_A, db_handle, &B_child, &block_commit)`. Assert `Err(ChainstateError::InvalidStacksBlock(_))`.
4. Node B (`lagging`): do NOT load `H_shadow` into its `staging_db` (simulate it not having downloaded/stored it yet). Call the same function with `staging_db_B`. Assert `Ok(())`.
5. Assert `result_A != result_B` for identical `(block, block_commit)` inputs, demonstrating the broken equality and that Node B would proceed to stage `B_child` as acceptable while Node A rejects it.

### Citations

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L217-238)
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

        if block_commit.parent_vtxindex != 0 {
            warn!("Invalid Nakamoto block: parent {} of {} is a shadow block but block-commit vtxindex is {}", &parent_header.block_id(), &block.block_id(), block_commit.parent_vtxindex);
            return Err(ChainstateError::InvalidStacksBlock("Invalid Nakamoto block: invalid block-commit parent vtxindex for parent shadow block".into()));
        }
```
