This confirms the equality holds. `NakamotoBlockHeader::consensus_hash` is explicitly documented as "the consensus hash of the burnchain block that selected this tenure" — i.e., it identifies the tenure's electing sortition, not the block's own gossip-time sortition height. [1](#0-0) 

In `relay.rs`'s unsolicited-block validation path, `sn` is fetched via `SortitionDB::get_block_snapshot_consensus(conn.conn(), &nakamoto_block.header.consensus_hash)`, and `sn_rc` is then `burnchain.block_height_to_reward_cycle(sn.block_height)`. [2](#0-1) 

This is exactly the same sortition lookup as `load_nakamoto_reward_set_for_tenure`'s `tenure_snapshot` parameter, whose doc comment states it "must be the snapshot of the sortition that elected the tenure (the sortition whose consensus hash the tenure's blocks carry)" — and internally it computes `reward_cycle = burnchain.block_height_to_reward_cycle(tenure_snapshot.block_height)`, identical to `sn_rc`. [3](#0-2) 

The block-storage path (`relay.rs::accept_block`-adjacent code) also derives `block_sn` the same way and calls `load_nakamoto_reward_set_for_tenure(&block_sn, ...)` directly. [4](#0-3) 

Both `sn` (P2P relay path) and `tenure_snapshot`/`block_sn` (chain-processing path) are resolved from the identical lookup key — `nakamoto_block.header.consensus_hash` — which is by construction the tenure's electing sortition, never the sortition at which the block was merely gossiped or processed. `NakamotoTenureEvent`/`check_nakamoto_tenure` further enforce that a block's `consensus_hash` must equal `tenure_payload.tenure_consensus_hash`, tying the tenure identity to the electing sortition, not to any later burn-tip height. [5](#0-4) 

There is no code path in `relay.rs` that derives the reward cycle from "the block's own sortition height" as distinct from its tenure-election sortition — the premise of the question is factually incorrect for this codebase. Both `verify_signer_signatures` call sites (P2P `handle_unsolicited_NakamotoBlocksData`-style validation and the canonical chain-processing `accept_block`-adjacent path) use the same `reward_set`, keyed off the same tenure-election sortition, so the claimed equality break does not exist. [6](#0-5) [7](#0-6) 

#No vulnerability found for this question.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L756-758)
```rust
    /// The consensus hash of the burnchain block that selected this tenure.  The consensus hash
    /// uniquely identifies this tenure, including across all Bitcoin forks.
    pub consensus_hash: ConsensusHash,
```

**File:** stackslib/src/net/relay.rs (L645-675)
```rust
        for nakamoto_block in nakamoto_blocks_data.blocks.iter() {
            // is this the right Stacks block for this sortition?
            let Some(sn) = SortitionDB::get_block_snapshot_consensus(
                conn.conn(),
                &nakamoto_block.header.consensus_hash,
            )?
            else {
                // don't know this sortition yet
                continue;
            };

            if !sn.pox_valid {
                info!(
                    "Pushed block from consensus hash {} corresponds to invalid PoX state",
                    nakamoto_block.header.consensus_hash
                );
                continue;
            }

            if !sn.sortition {
                info!(
                    "No such sortition in block with consensus hash {}",
                    &nakamoto_block.header.consensus_hash
                );
                return Err(net_error::InvalidMessage);
            }

            // is the block signed by the active reward set?
            let sn_rc = burnchain
                .block_height_to_reward_cycle(sn.block_height)
                .expect("FATAL: sortition has no reward cycle");
```

**File:** stackslib/src/net/relay.rs (L740-752)
```rust
            if let Err(e) = nakamoto_block
                .header
                .verify_signer_signatures(reward_set, epoch_id)
            {
                warn!(
                    "Signature verification failure for Nakamoto block";
                    "consensus_hash" => %nakamoto_block.header.consensus_hash,
                    "block_hash" => %nakamoto_block.header.block_hash(),
                    "reward_cycle" => sn_rc,
                    "error" => %e.to_string()
                );
                return Err(net_error::InvalidMessage);
            }
```

**File:** stackslib/src/net/relay.rs (L1015-1024)
```rust
        let tip = &block_sn.sortition_id;

        let reward_set = match load_nakamoto_reward_set_for_tenure(
            &block_sn,
            burnchain,
            chainstate,
            stacks_tip,
            sortdb,
            &OnChainRewardSetProvider::new(),
        ) {
```

**File:** stackslib/src/chainstate/nakamoto/coordinator/mod.rs (L359-381)
```rust
/// Load the reward set that was active when a Nakamoto tenure was elected.
///
/// `tenure_snapshot` must be the snapshot of the sortition that elected the tenure (the
/// sortition whose consensus hash the tenure's blocks carry), not the burnchain tip: a tenure
/// extended across a reward-cycle boundary is still signed by the reward set that was active
/// at its election. Load errors are folded into `ChainstateError` as block acceptance has
/// historically classified them.
pub fn load_nakamoto_reward_set_for_tenure<U: RewardSetProvider>(
    tenure_snapshot: &BlockSnapshot,
    burnchain: &Burnchain,
    chain_state: &mut StacksChainState,
    stacks_tip: &StacksBlockId,
    sort_db: &SortitionDB,
    provider: &U,
) -> Result<Option<RewardSet>, ChainstateError> {
    let reward_cycle = burnchain
        .block_height_to_reward_cycle(tenure_snapshot.block_height)
        .ok_or_else(|| {
            ChainstateError::Expects(format!(
                "Nakamoto tenure election at burn height {} has no reward cycle",
                tenure_snapshot.block_height
            ))
        })?;
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L659-666)
```rust
        // block header must match this tenure
        if block_header.consensus_hash != tenure_payload.tenure_consensus_hash {
            warn!("Invalid tenure-change (or block) -- mismatched consensus hash";
                  "tenure_payload.tenure_consensus_hash" => %tenure_payload.tenure_consensus_hash,
                  "block_header.consensus_hash" => %block_header.consensus_hash
            );
            return Ok(None);
        }
```
