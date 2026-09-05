### Title
Tenure-extend blocks are never checked against the actual tenure of their parent block, allowing extension across an unrelated tenure - (File: stackslib/src/chainstate/nakamoto/tenure.rs)

### Summary
`check_nakamoto_tenure`'s `Extended`/`ExtendedRuntime`/`Extended*` branch only checks that `tenure_payload.tenure_consensus_hash == tenure_payload.prev_tenure_consensus_hash` (self-consistency), but unlike the `BlockFound` branch it never compares these values against `parent_tenure.tenure_id_consensus_hash`, i.e. the *actual* tenure that `block_header.parent_block_id` belongs to. This lets an extend block claim consensus hash `X` while its `parent_block_id` genuinely resolves to a block from an unrelated tenure `Y`.

### Finding Description
The equality that should hold and is enforced for `BlockFound` but not for `Extended` causes is:
`parent_tenure.tenure_id_consensus_hash == tenure_payload.tenure_consensus_hash` (== `self.header.consensus_hash`).

For `TenureChangeCause::BlockFound`, `check_nakamoto_tenure` explicitly checks this: [1](#0-0) 

For the `Extended` family of causes, only the payload's *internal* self-consistency is checked (`tenure_consensus_hash == prev_tenure_consensus_hash`), with no comparison to `parent_tenure.tenure_id_consensus_hash` at all: [2](#0-1) 

`parent_tenure` itself is fetched purely by `block_header.parent_block_id` from the DB: [3](#0-2) 

The block-level well-formedness check in `nakamoto/mod.rs` (`is_wellformed_tenure_extend_block`) cannot detect this either, because it has no chainstate access — it only verifies that `tc_payload.previous_tenure_end == self.header.parent_block_id` (a raw hash equality) and that `tc_payload.tenure_consensus_hash`/`prev_tenure_consensus_hash` equal `self.header.consensus_hash`: [4](#0-3) 

Because none of these checks cross-validate the *actual* tenure of the referenced parent block against the extend block's declared `tenure_consensus_hash`, an attacker who controls the miner key for a legitimately-won sortition `X` can submit a follow-on `Extended` tenure-change block whose `previous_tenure_end`/`parent_block_id` points to a block belonging to a completely different, unrelated tenure `Y` (e.g., an orphaned/minority-fork block), rather than to the actual tip of tenure `X`'s own chain. `insert_nakamoto_tenure` will then persist a `nakamoto_tenure_events` row recording tenure `X` as "extended" on top of content from tenure `Y`, and the block header table will record `parent_block_id` pointing into `Y`'s chain.

The only other checks performed are the generic canonical-sortition checks (`check_valid_consensus_hash` on `tenure_sn`/`sortition_sn`, and `prev_sn.consensus_hash == tenure_sn.consensus_hash`, which is trivially satisfied since `prev_tenure_consensus_hash == tenure_consensus_hash` by construction) and the `previous_tenure_blocks` count check, which the attacker can trivially satisfy since they control/know the state of the block they choose as parent: [5](#0-4) 

None of `check_tenure_tx`, `validate_vrf_seed`, or the maturation window logic reference `parent_tenure.tenure_id_consensus_hash` for the extend path either, so the divergence is not caught anywhere downstream in this call path.

### Impact Explanation
An extend block wrongfully accepted with a parent from an unrelated tenure grafts one miner's tenure onto another tenure's block history. This corrupts the tenure/coinbase-height bookkeeping (`nakamoto_tenure_events`, `height_in_tenure`) used for reward maturation and can cause two honest nodes to disagree about which chain of blocks constitutes tenure `X`, i.e. a chain split / invalid-block-accepted condition. It also risks double-counting or misattributing coinbase maturation windows tied to `parent_coinbase_height`, which is a Critical-class impact per the stated criteria (invalid block accepted network-wide / chain split).

### Likelihood Explanation
The attacker needs only: (1) to win a single sortition normally (achievable with minority stake, as is expected for any miner slot), and (2) to have or produce some earlier block from a different tenure to reference as `parent_block_id`/`previous_tenure_end` (e.g., a minority-fork block they previously mined, which is realistic since miners routinely produce blocks on minority forks that get orphaned). No signer majority, no node compromise, and no other miner's key are required — this fits within the unprivileged, minority-stake threat model.

### Recommendation
In `check_nakamoto_tenure` (stackslib/src/chainstate/nakamoto/tenure.rs), add a check in the `Extended*` match arm requiring `parent_tenure.tenure_id_consensus_hash == tenure_payload.tenure_consensus_hash`, mirroring the check already present for `BlockFound` (`parent_tenure.tenure_id_consensus_hash != tenure_payload.prev_tenure_consensus_hash`), so that an extend block can only extend the tenure that its actual parent block belongs to.

### Proof of Concept
Rust integration test plan (nakamoto chainstate test harness, e.g. building on `stackslib/src/chainstate/nakamoto/tests/mod.rs` patterns):
1. Produce tenure `Y` with a `BlockFound` tenure-change and one follow-on block `B_Y`.
2. Produce a separate, unrelated tenure `X` via a real sortition and `BlockFound` block `B_X` (parent legitimately pointing to the real prior tenure).
3. Craft an `Extended` tenure-change block `E` with:
   - `header.consensus_hash = X`
   - `header.parent_block_id = B_Y.block_id()`
   - `tc_payload.tenure_consensus_hash = X`, `tc_payload.prev_tenure_consensus_hash = X`
   - `tc_payload.previous_tenure_end = B_Y.block_id()`
   - `tc_payload.previous_tenure_blocks` set to the correct `height_in_tenure` of `B_Y`
4. Call `NakamotoChainState::check_nakamoto_tenure(...)` (or the full `advance_nakamoto_tenure`) on `E`.
5. Assert BEFORE/AFTER equality: `parent_tenure.tenure_id_consensus_hash` (looked up for `B_Y`, expected `== Y`) is **not** equal to `tc_payload.tenure_consensus_hash` (`== X`); assert that the function currently returns `Ok(Some(parent_tenure))` (bug) instead of the expected `Ok(None)`/rejection once the missing check is present.

### Citations

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

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L774-788)
```rust
        match tenure_payload.cause {
            TenureChangeCause::BlockFound => {
                // this tenure_payload's prev_consensus_hash must match the parent block tenure's
                // tenure_consensus_hash -- i.e. this tenure must be distinct from the parent
                // block's tenure
                if parent_tenure.tenure_id_consensus_hash
                    != tenure_payload.prev_tenure_consensus_hash
                {
                    warn!("Invalid tenure-change: tenure block-found does not confirm parent block's tenure";
                          "parent_tenure.tenure_consensus_hash" => %parent_tenure.tenure_id_consensus_hash,
                          "prev_consensus_hash" => %tenure_payload.prev_tenure_consensus_hash
                    );
                    return Ok(None);
                }
            }
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L789-806)
```rust
            TenureChangeCause::Extended
            | TenureChangeCause::ExtendedRuntime
            | TenureChangeCause::ExtendedReadCount
            | TenureChangeCause::ExtendedReadLength
            | TenureChangeCause::ExtendedWriteCount
            | TenureChangeCause::ExtendedWriteLength => {
                // tenure extensions don't begin a new tenure (since the miner isn't changing), so
                // the tenure consensus hash must be the same as the previous tenure consensus hash
                if tenure_payload.tenure_consensus_hash != tenure_payload.prev_tenure_consensus_hash
                {
                    warn!("Invalid tenure-change: tenure extension tries to start a new tenure";
                          "tenure_consensus_hash" => %tenure_payload.tenure_consensus_hash,
                          "prev_tenure_consensus_hash" => %tenure_payload.prev_tenure_consensus_hash,
                    );
                    return Ok(None);
                }
            }
        };
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L807-824)
```rust

        // The tenure-change must report the number of blocks _so far_ in the previous tenure (note if this is a TenureChangeCause::Extended, then its parent tenure will be its own tenure).
        // If there is a succession of tenure-extensions for a given tenure, then the reported tenure
        // length must report the number of blocks since the last _sortition-induced_ tenure
        // change.
        let tenure_len =
            Self::get_nakamoto_tenure_length(headers_conn.sqlite(), &block_header.parent_block_id)?;

        if tenure_len != tenure_payload.previous_tenure_blocks {
            // invalid -- does not report the correct number of blocks in the past tenure
            warn!("Invalid tenure-change: wrong number of blocks";
                  "tenure_consensus_hash" => %tenure_payload.tenure_consensus_hash,
                  "prev_tenure_consensus_hash" => %tenure_payload.prev_tenure_consensus_hash,
                  "tenure_len" => tenure_len,
                  "tenure_payload.previous_tenure_blocks" => tenure_payload.previous_tenure_blocks
            );
            return Ok(None);
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1466-1492)
```rust
        if tc_payload.previous_tenure_end != self.header.parent_block_id {
            // discontinuous
            warn!(
                "Invalid block -- discontiguous";
                "previous_tenure_end" => %tc_payload.previous_tenure_end,
                "parent_block_id" => %self.header.parent_block_id,
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id()
            );
            return Err(err);
        }

        if tc_payload.tenure_consensus_hash != self.header.consensus_hash
            || tc_payload.prev_tenure_consensus_hash != self.header.consensus_hash
        {
            // tenure-extends don't change the current miner
            warn!(
                "Invalid block -- tenure extend tx must have the same consensus hash and previous consensus hash as the block header";
                "tenure_consensus_hash" => %tc_payload.tenure_consensus_hash,
                "prev_tenure_consensus_hash" => %tc_payload.prev_tenure_consensus_hash,
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id()
            );
            return Err(err);
        }
```
