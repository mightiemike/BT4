### Title
Static validation of block parent only compares heights, not block identity, allowing signers to accept blocks built on divergent tenure siblings - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
`NakamotoChainState::check_block_builds_on_highest_block_in_tenure` (invoked from `check_block_has_valid_parent`, `stackslib/src/net/api/postblock_proposal.rs` lines ~474-524) only validates that the proposed block's parent has the *same height* as the tenure's locally-known highest block, never that the parent's *hash/identity* matches that highest block. [1](#0-0)  Because `find_highest_known_block_header_in_tenure` resolves "highest" from each node's own locally-received staging blocks, two signers who receive two same-height tenure siblings in different gossip order can each treat a different sibling as "highest," yet both will pass a subsequent block's validation as long as heights line up — the code never checks the two headers are the same block.

### Finding Description
The claimed equality is: *"highest-in-tenure" block as computed by signer A == "highest-in-tenure" block as computed by signer B* at the moment a subsequent block C is validated. The code path is:

`check_block_has_valid_parent` -> (non-tenure-start branch) -> `check_block_builds_on_highest_block_in_tenure(chainstate, sortdb, consensus_hash, parent_block_id)` [2](#0-1) 

Inside `check_block_builds_on_highest_block_in_tenure`, the function fetches `highest_header` via `find_highest_known_block_header_in_tenure` (a query over the node's own local staging-block view) and `parent_header` via `get_block_header(parent_block_id)`, then performs the sole correctness check:

```
if parent_header.anchored_header.height() != highest_header.anchored_header.height() {
    ... reject ...
}
``` [3](#0-2) 

This compares `.height()` values only — it never compares `parent_header.index_block_hash() == highest_header.index_block_hash()` (or `consensus_hash`+block hash). If two non-tenure-start sibling blocks B1 and B2 (both children of the same real parent P, both at height N+1) exist because a minority miner released two competing blocks in the same tenure, then:
- Signer A, who received B1 first, computes `highest_header = B1` (height N+1).
- Signer B, who received B2 first, computes `highest_header = B2` (height N+1).

A subsequent proposal C, whose `parent_block_id` names B1 (height N+1), will pass validation on *both* signers: on Signer A because `parent_header (B1).height() == highest_header (B1).height()`, and on Signer B because `parent_header (B1).height() == highest_header (B2).height()` — both are N+1, so the check succeeds even though Signer B's "highest" block (B2) is not actually B1. The height-only comparison silently accepts a parent that is a *different block* than the one the validating node itself considers canonical/highest, so this static check provides no real assurance that the proposal builds on the node's own view of the tenure tip — it only checks that heights coincidentally match.

Existing guards do not catch this: `check_block_has_valid_tenure` only verifies the consensus hash is on the canonical sortition fork, not block identity within the tenure; there is no assertion elsewhere in this validation path that compares full block IDs between the local highest header and the referenced parent header.

### Impact Explanation
This allows two signers (both honest, unprivileged w.r.t. each other) to validate/sign as "acceptable" two divergent block-building sequences within the same tenure, because the static parent check cannot distinguish "the parent is my locally-known highest block" from "the parent merely happens to be at the same height as my locally-known highest block." This is a minority-triggerable, static-validation divergence causing a bounded, temporary tip disagreement among signers in a single tenure — matching the High-severity category (not a full chain split, since only one branch will ultimately be canonicalized by the sortition/tenure rules, but signers can be induced to accept/sign blocks extending non-canonical siblings they did not actually recognize as the tip).

### Likelihood Explanation
Preconditions: a minority miner wins a single sortition slot (no majority stake/signers needed) and broadcasts two sibling non-tenure-start blocks that reach different signers in different gossip order before convergence — entirely feasible given normal P2P propagation variance and requires no majority stake, no signer key compromise, and only the cost of one sortition win. This is realistically repeatable any time a miner (even a minority one) chooses to build two competing blocks within one tenure and network latency causes signers to disagree transiently on which arrived "first."

### Recommendation
Change the check in `check_block_builds_on_highest_block_in_tenure` to compare full block identity, not just height:
```
if parent_header.index_block_hash() != highest_header.index_block_hash() {
    // reject
}
```
(retaining the height comparison only as a diagnostic/logging aid), so that a proposal is only accepted if its parent is exactly the block the validating node itself recognizes as the tenure's highest block.

### Proof of Concept
Rust integration test plan (two-signer/two-node harness, extending existing tests in `stackslib/src/chainstate/nakamoto/tests/mod.rs` which already exercises `check_block_has_valid_parent`/`check_block_builds_on_highest_block_in_tenure`):
1. Set up a tenure with parent block P at height N.
2. Construct two sibling non-tenure-start blocks B1 and B2, both with `parent_block_id = P`, both at height N+1, differing in some field (e.g., differing tx set) so they hash differently.
3. Instantiate two chainstate/staging-block views (simulating Signer A and Signer B): feed B1 then B2 to view A (so `find_highest_known_block_header_in_tenure` returns B1); feed B2 then B1 to view B (so it returns B2).
4. Construct block C with `parent_block_id = B1.index_block_hash()`, height N+2.
5. Call `NakamotoChainState::check_block_builds_on_highest_block_in_tenure` (or `check_block_has_valid_parent`) against both view A and view B with block C.
6. Assert the equality break: view A returns `Ok(())` (expected) AND view B *also* returns `Ok(())` even though view B's `highest_header` is B2, not B1 — i.e., assert `highest_header_A.index_block_hash() != highest_header_B.index_block_hash()` while both validations succeed for the same block C, demonstrating the divergence that a hash-identity check would have caught (and would, after the fix, cause view B to return `Err(InvalidParentBlock)`).

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L415-450)
```rust
        let Some(parent_header) =
            NakamotoChainState::get_block_header(chainstate.db(), parent_block_id).map_err(
                |e| BlockValidateRejectReason {
                    reason_code: ValidateRejectCode::ChainstateError,
                    reason: format!("Failed to query block header by block ID: {:?}", &e),
                    failed_txid: None,
                },
            )?
        else {
            warn!(
                "Rejected block proposal";
                "reason" => "Block has no parent",
                "parent_block_id" => %parent_block_id
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::UnknownParent,
                reason: "Block has no parent".into(),
                failed_txid: None,
            });
        };
        if parent_header.anchored_header.height() != highest_header.anchored_header.height() {
            warn!(
                "Rejected block proposal";
                "reason" => "Block's parent is not the highest block in this tenure",
                "consensus_hash" => %tenure_id,
                "parent_header.height" => parent_header.anchored_header.height(),
                "highest_header.height" => highest_header.anchored_header.height(),
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::InvalidParentBlock,
                reason: "Block is not higher than the highest block in its tenure".into(),
                failed_txid: None,
            });
        }
        Ok(())
    }
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L494-502)
```rust
        if !is_tenure_start {
            // this is a well-formed block that is not the start of a tenure, so it must build
            // atop an existing block in its tenure.
            Self::check_block_builds_on_highest_block_in_tenure(
                chainstate,
                sortdb,
                &block.header.consensus_hash,
                &block.header.parent_block_id,
            )?;
```
