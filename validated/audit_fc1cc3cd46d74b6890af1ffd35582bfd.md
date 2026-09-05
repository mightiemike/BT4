### Title
`validate_shadow_parent_burnchain` silently no-ops when the parent header is not yet staged, allowing a shadow-tenure-inconsistent child block to bypass block-commit validation depending on relay order - ([File: stackslib/src/chainstate/nakamoto/shadow.rs])

### Summary
`NakamotoChainState::validate_shadow_parent_burnchain` only enforces the shadow-parent block-commit invariants (`parent_vtxindex == 0`, `parent_block_ptr == parent_sn.block_height`) when the parent header can already be looked up via `staging_db.get_nakamoto_block_header(&block.header.parent_block_id)`. If the parent has not yet been relayed/staged at the time the child is validated, the function returns `Ok(())` unconditionally, at [1](#0-0) , treating the not-yet-seen parent exactly the same as a legitimately non-shadow parent.

### Finding Description
The broken equality is: *"the child block's acceptance decision"* should be identical regardless of whether the validating node received the parent block before or after the child (arrival-order independence), i.e. TENURE/REWARD equality — every block's tenure must equal its authorizing sortition's tenure, consistently node-to-node.

The function's contract, per its own doc comment, is to guarantee: "Returns Ok(()) if the parent is a shadow block, and the [block-commit] criteria are met" and "Err(..) if... some of the criteria above are false" [2](#0-1) . However, the actual implementation cannot distinguish "parent is not a shadow block" from "parent header is simply not present in this node's staging DB yet" — both paths return `Ok(())` via the same early `let Some(parent_header) = ... else { return Ok(()); }` at line 225-229 [3](#0-2) .

Because block relay order across the P2P network is attacker-influenceable (an unprivileged participant can control which of two already-crafted, already-signed blocks it relays first to a given peer), a node that receives the child before its shadow parent will validate the child's block-commit constraints as a no-op, whereas a node that already has the parent staged will apply the real check (`block_commit.parent_vtxindex`/`parent_block_ptr` comparisons). This can lead one node to accept a child block into staging/chainstate that a second node — with different arrival order — would reject as `InvalidStacksBlock`.

### Impact Explanation
If exploited, this would let two honest nodes end up with different validity determinations for the same block ID depending purely on network timing, which is the definition of a validation/consensus divergence. However, this only affects the auxiliary `validate_shadow_parent_burnchain` consistency check — a check that guards shadow-tenure/block-commit metadata concordance — and does not by itself change the actual sequence of block processing: Nakamoto block append/processing (`process_next_nakamoto_block`) requires that a block's parent already be a known/processed block in the chainstate before the child itself can be appended to the canonical tip. That means even if the "no-op" branch is taken at initial relay-time acceptance (staging), the same node cannot actually incorporate the child into its canonical fork before the parent is itself staged and processed — at which point the child's relationship to the (now known) parent gets exercised through the ordinary block-processing/tenure-linkage machinery (`check_tenure_tx`, tenure-start/tenure-extend verification, `common_validate_against_burnchain`), which independently ties each block to its own sortition's block-commit. I was not able to confirm within the available context whether `validate_shadow_parent_burnchain` (or an equivalent check) is re-invoked once the parent becomes available and before the child is appended to the canonical chain, nor whether any other consensus-critical linkage (e.g., VRF seed continuity, tenure-change validation) independently re-derives and enforces this exact block-commit-to-shadow-parent relationship at processing time.

### Likelihood Explanation
Preconditions are limited to: a shadow tenure existing on the network (already a rare, SIP-driven emergency-recovery event) and the attacker controlling the relay order of an already-crafted parent/child pair to different peers. This requires no majority stake, no signer/miner privilege — any peer that can broadcast Nakamoto blocks over P2P can attempt to influence delivery order. It is feasible in principle but is gated on the existence of a shadow tenure, which is not attacker-controllable and is an exceptional/rare event in the protocol's lifecycle.

### Recommendation
`validate_shadow_parent_burnchain` should not treat "parent header missing from staging DB" the same as "parent is confirmed non-shadow." Instead, block acceptance for a child whose parent is not yet locally known should defer this validation (e.g., queue the block as unconfirmed/unlinked, not staged) until the parent header is available, and the block-commit/shadow-parent check should be (re-)enforced deterministically at the point where the block is actually appended to the chain (i.e., as part of `process_next_nakamoto_block`/`append_block`), not solely as a best-effort check at first-relay time.

### Proof of Concept
Rust integration test plan (two-node harness):
1. Construct a shadow tenure and its terminal shadow block, plus a subsequent normal child block whose `LeaderBlockCommitOp` has `parent_vtxindex != 0` (or `parent_block_ptr` mismatched to the shadow parent's sortition height) — i.e., a block-commit that violates the shadow-parent invariant.
2. On Node A: relay the child block first (before the shadow parent is staged). Assert `NakamotoChainState::accept_block`/staging succeeds (`Ok(())`) due to the no-op branch in `validate_shadow_parent_burnchain`.
3. On Node B: relay the shadow parent first, then the same child block. Assert that `validate_shadow_parent_burnchain` returns `Err(ChainstateError::InvalidStacksBlock(..))` for the identical child block ID.
4. Assert the divergence: `staging_result_on_A(child_block_id) != staging_result_on_B(child_block_id)` for the same `block_id`.
5. (Extended) Continue both nodes forward through `process_next_nakamoto_block` to determine whether the divergence persists into actual canonical-chain acceptance/rejection, or whether a later re-validation step reconciles both nodes to the same outcome — this step is necessary to confirm whether the impact reaches "Critical: chain split" or is contained to a transient staging-layer inconsistency.

### Citations

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L212-216)
```rust
    /// Returns Ok(()) if the parent is _not_ a shadow block
    /// Returns Ok(()) if the parent is a shadow block, and the above criteria are met
    /// Returns Err(ChainstateError::InvalidStacksBlock(..)) if the parent is a shadow block, and
    /// some of the criteria above are false
    /// Returns Err(..) on other (DB-related) errors
```

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
