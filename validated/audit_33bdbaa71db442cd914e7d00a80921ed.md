### Title
Same-tenure sibling blocks never trigger a signing-weight tie-break in `set_stacks_block_accepted_at_tip`, allowing the canonical Nakamoto tip to diverge permanently between nodes — ([File: stackslib/src/chainstate/burn/db/sortdb.rs])

### Summary
`store_block_if_better` in `stackslib/src/chainstate/nakamoto/staging_blocks.rs` only performs a `signing_weight` comparison when two stored blocks share the *same* `(consensus_hash, block_hash)` key, i.e. literal malleablized copies of the identical signed content. Two genuinely different blocks (different content, different `block_hash`) that both belong to the same tenure and land at the same chain height are each stored as independent rows with no weight arbitration between them, and the downstream tip-selection logic in `SortitionDB::set_stacks_block_accepted_at_tip` (`stackslib/src/chainstate/burn/db/sortdb.rs:1968-2050`) explicitly refuses to ever replace the canonical tip when the competing block is in the *same* tenure at the *same* height — regardless of which one carries more signer weight.

### Finding Description
The broken equality is: `NakamotoChainState::get_canonical_block_header(node_A)` must equal `NakamotoChainState::get_canonical_block_header(node_B)` once both B1 and B2 are known to both nodes.

Trace:
1. `NakamotoChainState::accept_block` computes `signing_weight` via `header.verify_signer_signatures(reward_set, epoch_id)` (`stackslib/src/chainstate/nakamoto/mod.rs:2917-2937`) and calls `staging_db_tx.store_block_if_better(block, burn_attachable, signing_weight, obtain_method)`.
2. `store_block_if_better` (`stackslib/src/chainstate/nakamoto/staging_blocks.rs:603-663`) first tries `try_store_block_with_new_signer_sighash`, which stores *any* block whose `(consensus_hash, block_hash)` pair is new — this is unconditional, no weight check (`staging_blocks.rs:753-766`). The weight comparison (`existing_signing_weight < signing_weight` → `replace_block`) only fires in "case 2", when a row with the *same* `block_hash` already exists (i.e., the identical signed payload, re-obtained by another path). Two distinct, fully-signed blocks B1 and B2 with different content (and therefore different `block_hash`) both take the "case 1" path on every node and are stored as separate staging rows — no arbitration occurs here at all.
3. Both B1 and B2 are valid candidates for `next_ready_nakamoto_block` (`staging_blocks.rs:466-528`), which selects any unprocessed, non-orphaned, burn-attachable child whose parent is processed, ordered only by `height ASC` — it does not consider `signing_weight` or arrival order beyond height, so whichever sibling a given node encounters/downloads first gets processed first on that node.
4. When a block is processed, `SortitionDB::set_stacks_block_accepted_at_tip` (`sortdb.rs:1968-2050`) decides whether to move the canonical Nakamoto tip. Its logic:
   - `cur_height < stacks_block_height` → replace (higher block wins)
   - `cur_height > stacks_block_height` → don't replace
   - `cur_height == stacks_block_height` **and** `cur_ch == consensus_hash` (i.e., same tenure, same height — exactly the sibling case) → `false`, **never replace**, with no reference to `signing_weight` whatsoever
   - only when the tie is across *different* tenures does it fall back to a "latest sortition wins" tie-break — still not a signing-weight comparison.

   The code comment claims the height-based replacement exists "because it represents more overall signer votes," but that rationale is never actually checked for the same-tenure/same-height branch — it is a hard `false`, permanently locking in whichever sibling was processed first at that node.

Consequence: once node A processes B1 first, its tip is pinned to B1 forever with respect to that tenure/height, even after B2 (higher weight) is later downloaded, validated, and successfully processed into staging as `processed = true`. Symmetrically, node B is pinned to B2. No subsequent event forces the lower-weight tip to be superseded by the higher-weight one because that comparison never occurs at the sortition-DB tip-selection layer for same-tenure siblings.

Existing guards that were checked and do not prevent this:
- `verify_signer_signatures` only enforces the ≥70% threshold on a per-block basis; it has no knowledge of a competing sibling and does not encode which of two independently-thresholded blocks should be preferred.
- `check_tenure_tx` / `validate_*_static` / `common_validate_against_burnchain` validate a single block against burnchain/tenure structure but do not perform any cross-sibling arbitration.
- The signer-side guard (`check_block_against_signer_db_state`, `conflict_still_blocks`, documented in `docs/signer-flows.md`) is a *signer* mitigation intended to stop honest signers from double-signing across conflicting blocks, but the audit's own preconditions explicitly place "a race between two legitimately fully-signed blocks for the same slot" in scope — i.e., we are told to assume the signer-side guard was raced/bypassed and both blocks did reach threshold. Once that happens, there is no chainstate-level (node-side) mechanism that re-converges the tip by weight.

### Impact Explanation
This is a genuine chain split: two honest, fully-synced nodes with identical inputs (both B1 and B2, both fully signed and valid) can permanently disagree on `get_canonical_block_header`/canonical STATE_ROOT for the same tenure, because the weight-based tie-break that `store_block_if_better` implements for identical-content re-signs is never applied to genuinely different sibling blocks, and the sortition-DB tip-selection logic hard-codes "first processed wins" for same-tenure/same-height conflicts. This matches the Critical category ("a chain split or deep fork ... a non-reproducible state root") since the divergence does not self-heal once complete information is available at both nodes.

### Likelihood Explanation
The precondition — two disjoint signer coalitions each independently reaching the 70% threshold for two different blocks at the same tenure/height — is stated as in-scope for this question ("a race between two legitimately fully-signed blocks for the same slot is in scope"), and is explicitly acknowledged as a possible real-world event in the codebase's own comment in `sortdb.rs` describing "benign forks" from conflicting tenure-changes (e.g., a late tenure-change signed by overlapping-but-different signer subsets due to timing). It requires no majority stake and no privileged role from the attacker — only the ability to broadcast two competing, honestly-signed blocks (or to exploit natural signer-network latency/partition) to different halves of the network first. I was not able to fully verify, given tool-call limits, whether an additional mempool/tenure-level consensus check elsewhere (outside `nakamoto/mod.rs`, `staging_blocks.rs`, and `sortdb.rs`) forcibly orphans same-height siblings before this code path is reached; this should be confirmed with a live two-node reproduction.

### Recommendation
In `SortitionDB::set_stacks_block_accepted_at_tip`, extend the `cur_ch == consensus_hash` (same tenure, same height) branch to compare `signing_weight` (or an equivalent persisted weight column looked up via the Nakamoto staging table) between the current tip's block and the newly-processed block, and replace the tip when the new block carries strictly more signing weight — mirroring the intent already implemented for `store_block_if_better`'s same-`block_hash` case. This tie-break must be applied identically and deterministically by every node whenever it becomes aware of both blocks, not merely once, so that reprocessing/eventual convergence is guaranteed once full information is available network-wide.

### Proof of Concept
Rust integration test plan (two-node/two-fork harness, extending patterns already used in `stackslib/src/chainstate/nakamoto/tests/mod.rs` and `stackslib/src/net/tests/relay/nakamoto.rs`):

1. Build a `RewardSet` with three signers of weight 34/33/33 (`make_reward_set` helper style from `stackslib/src/chainstate/nakamoto/tests/mod.rs:3668`), so any two form a threshold-clearing majority.
2. Construct two `NakamotoBlock`s, B1 and B2, both children of the same processed parent, same `consensus_hash` (tenure), same `chain_length` (height), but with different transaction payloads (yielding different `signer_signature_hash`/`block_hash`).
3. Sign B1 with signers {1,2} (weight 67) and B2 with signers {2,3} or {1,3} (weight ≥67, choose so `weight(B2) > weight(B1)`), each independently satisfying `verify_signer_signatures`.
4. Two chainstate instances (`NodeA`, `NodeB`):
   - `NodeA`: call `NakamotoChainState::accept_block`/`process_next_nakamoto_block` with B1 first (asserting it's stored+processed and becomes tip), then feed B2 and process it.
   - `NodeB`: feed B2 first (processed, becomes tip), then feed B1 and process it.
5. After both nodes have processed both blocks:
   - Assert `NakamotoChainState::get_canonical_block_header(NodeA.chainstate.db(), NodeA.sortdb)` and the same call on `NodeB` return the *same* header (expected: the higher-weight block, B2).
   - Current expected (bug) result: `NodeA`'s tip remains B1 and `NodeB`'s tip remains B2 — the assertion of equality fails, demonstrating the permanent split.
6. Additionally assert directly on `SortitionDB::get_canonical_nakamoto_tip_hash_and_height` before/after processing the second block on each node to show `set_stacks_block_accepted_at_tip`'s `cur_ch == consensus_hash` branch returning `false` unconditionally, regardless of `signing_weight`.