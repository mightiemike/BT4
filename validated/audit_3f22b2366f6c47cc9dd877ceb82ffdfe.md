### Title
Missing tenure-continuity check in `check_nakamoto_tenure`'s `Extended*` branch lets a tenure-extend block graft onto a foreign sibling tenure - (File: stackslib/src/chainstate/nakamoto/tenure.rs)

### Summary
`check_nakamoto_tenure` fetches `parent_tenure = get_ongoing_tenure(block_header.parent_block_id)` (tenure.rs:765-771) and uses it to validate `TenureChangeCause::BlockFound` payloads (tenure.rs:779-787), but for all `Extended*` causes (tenure.rs:789-805) it only checks internal self-consistency of the payload (`tenure_payload.tenure_consensus_hash != tenure_payload.prev_tenure_consensus_hash`) and never compares either field against `parent_tenure.tenure_id_consensus_hash`. This omits the invariant that a tenure-extend must extend the *same* tenure that its parent block actually belongs to.

### Finding Description
The equality that must hold is:
`get_ongoing_tenure(block_header.parent_block_id).tenure_id_consensus_hash == tenure_payload.tenure_consensus_hash`

For the `BlockFound` cause this is enforced indirectly (parent tenure must differ from and precede the new tenure) at tenure.rs:779-787. For the `Extended`/`ExtendedRuntime`/`ExtendedReadCount`/`ExtendedReadLength`/`ExtendedWriteCount`/`ExtendedWriteLength` causes, the code at tenure.rs:789-805 only checks:
```rust
if tenure_payload.tenure_consensus_hash != tenure_payload.prev_tenure_consensus_hash {
    ... return Ok(None);
}
```
`parent_tenure`, computed just above at tenure.rs:765-771, is never referenced inside this match arm. The only other structural checks are: `block_header.consensus_hash == tenure_payload.tenure_consensus_hash` (tenure.rs:660), `tenure_payload.previous_tenure_end == block_header.parent_block_id` (tenure.rs:669), and canonical-fork/snapshot-ordering checks against `tenure_sn`/`sortition_sn`/`prev_sn` (tenure.rs:678-751) — none of which compare the *actual* tenure that `parent_block_id` resides in against `tenure_consensus_hash`.

An attacker who won sortition CH_A can therefore craft an `Extended` tenure-change payload with `tenure_consensus_hash = prev_tenure_consensus_hash = CH_A` (satisfying tenure.rs:797) while setting `block_header.parent_block_id` (and matching `previous_tenure_end`) to a block that actually lives in sibling tenure CH_B. `check_valid_consensus_hash` only confirms CH_A and CH_B are each canonical, valid snapshots (tenure.rs:678-706) — it does not confirm the parent block's *own* tenure equals CH_A. As a result the function returns `Ok(Some(parent_tenure))` with `parent_tenure.tenure_id_consensus_hash == CH_B`, silently accepting a block tagged as tenure CH_A that is really chained onto CH_B's history.

### Impact Explanation
Two blocks can then both legitimately claim to be part of tenure CH_A (the genuine one, if any, and the grafted one built on CH_B), or a tenure-extend record can point into a tenure lineage the miner never actually owns at that point in the fork. Because `advance_nakamoto_tenure` (tenure.rs:835-878) uses this same `check_nakamoto_tenure` result to decide whether the coinbase height advances and to insert tenure records, this can let two divergent branches share/confuse a coinbase height and its tenure bookkeeping, which is a chain-split/reward-accounting class issue (Critical per the stated scope) if it propagates into node disagreement about canonical state.

### Likelihood Explanation
Preconditions: the attacker needs to have legitimately won at least one sortition producing consensus hash CH_A (a normal, unprivileged single-miner-slot outcome), and CH_B must be a canonical sibling tenure with a valid, previously-accepted block. No majority stake, no signer collusion, and no additional privileges are required to construct the malformed payload itself — the gap is purely a missing comparison in `check_nakamoto_tenure`. However, I was not able to fully verify, within the available tool budget, whether this crafted block would still be rejected by other layers before/after this check — e.g., the signer-side block validation path, `NakamotoChainState::append_block`'s state-root/MARF continuity logic, or `check_tenure_continuity` (tenure.rs:887-923, which explicitly enforces this exact equality but only for blocks *without* a tenure-change tx and thus may not run here). This uncertainty is material to whether the gap is truly end-to-end exploitable or is masked by a redundant check elsewhere in the append pipeline.

### Recommendation
In the `Extended*` match arm (tenure.rs:789-805), add an explicit check that `parent_tenure.tenure_id_consensus_hash == tenure_payload.tenure_consensus_hash` (equivalently, `== tenure_payload.prev_tenure_consensus_hash`, given the existing equality), mirroring the invariant already enforced for `BlockFound`, and reject the tenure-change (`Ok(None)`) if it does not hold.

### Proof of Concept
Rust integration test plan (extend an existing test harness such as `stackslib/src/chainstate/nakamoto/tests/mod.rs` or `coordinator/tests.rs`):
1. Build two sibling Nakamoto tenures off a common parent via `BlockFound`, producing canonical consensus hashes CH_A and CH_B with valid snapshots for both.
2. Mine at least one child block into CH_B's chain to obtain a real `parent_block_id`.
3. Craft a `NakamotoBlockHeader` with `consensus_hash = CH_A` and `parent_block_id` set to the CH_B block, and a `TenureChangePayload` with `cause = TenureChangeCause::Extended`, `tenure_consensus_hash = CH_A`, `prev_tenure_consensus_hash = CH_A`, `previous_tenure_end` matching `parent_block_id`.
4. Call `NakamotoChainState::check_nakamoto_tenure` directly with this header/payload.
5. Assert (pre-fix) that it returns `Ok(Some(parent_tenure))` with `parent_tenure.tenure_id_consensus_hash == CH_B != tenure_payload.tenure_consensus_hash (CH_A)` — i.e., the equality `get_ongoing_tenure(parent_block_id).tenure_id_consensus_hash == tenure_payload.tenure_consensus_hash` is violated and accepted.
6. Assert (post-fix) that the same call returns `Ok(None)`.