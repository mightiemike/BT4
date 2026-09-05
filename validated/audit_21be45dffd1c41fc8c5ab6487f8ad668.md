### Title
Signature check on unsolicited Nakamoto blocks uses a peer's stale `current_reward_sets` cache, causing per-node validation-verdict divergence - (File: stackslib/src/net/unsolicited.rs)

### Summary
`PeerNetwork::check_nakamoto_block_signer_signature` verifies an incoming Nakamoto block's signer signatures against a reward set pulled from the peer's own in-memory `current_reward_sets` cache [1](#0-0) , rather than the reward set freshly loaded from chainstate at block-processing time (as `relay.rs`/`nakamoto/mod.rs` do via `load_nakamoto_reward_set_for_tenure`/`load_nakamoto_reward_set`). This cache is only refreshed opportunistically by `PeerNetwork::refresh_reward_cycles`, gated by `check_reload_cached_reward_set`, which in epoch 3.x invalidates purely on detecting a "reorg" [2](#0-1) .

### Finding Description
The relay/validation fast-path for unsolicited Nakamoto blocks answers "is this signature set valid?" using whatever `RewardSet` happens to be sitting in `self.current_reward_sets` for the block's reward cycle [3](#0-2) . That cache is populated by `refresh_reward_cycles`, which only recomputes an entry when `check_reload_cached_reward_set` returns true — and in Nakamoto (Epoch ≥ 3.0) that function's *only* invalidation trigger is a detected reorg (`is_reorg` / `is_nakamoto_reorg`) [4](#0-3) . There is no invalidation path tied to a same-fork, later re-write of the `.signers` reward set (e.g. the reward set for a cycle being (re)computed/persisted after this node already cached an earlier snapshot for that cycle, or the node simply not yet having refreshed since the signer set was written). Meanwhwile, the canonical block-acceptance path used elsewhere (`relay.rs::validate_nakamoto_blocks`, `nakamoto/mod.rs::process_next_nakamoto_block`) always reloads the reward set live from chainstate for the specific `parent_block_id`/fork in question [5](#0-4) [6](#0-5) .

This is the direct analog of the `MaltDataLab` bug: a consensus-adjacent decision (signature validity) is made against a value populated by an out-of-band refresh routine rather than being derived transactionally from the state being validated, and the refresh routine's invalidation condition (reorg-only) does not cover every state transition that can change the underlying reward set for an already-cached cycle.

Because two honest, non-Byzantine nodes can have `current_reward_sets` populated at different wall-clock times relative to a reward-set-affecting event (each node calls `refresh_reward_cycles` independently, on its own poll cadence, off of `refresh_burnchain_view`), one node can evaluate `check_nakamoto_block_signer_signature` against a stale reward set (accepting or rejecting a block's signatures) while a second node — which has already refreshed — evaluates the same signatures against the current reward set and reaches the opposite verdict. This breaks the invariant that all honest nodes should agree on whether a given Nakamoto block's signer signatures are valid.

### Impact Explanation
This falls under "a validation verdict two nodes disagree on" (High). The practical severity is bounded by the fact that `check_nakamoto_block_signer_signature` gates only the *unsolicited-message* fast path (used to decide whether to accept/relay a gossiped block, `is_nakamoto_block_bufferable`/related unsolicited-message handling) — not the authoritative `NakamotoChainState::append_block` / staging-DB acceptance path, which always reloads the reward set fresh. So the worst-case blast radius is p2p-level: one node may drop/refuse-to-relay a block that other nodes accept (or vice versa), producing transient tip/propagation disagreement and possible delayed convergence, rather than an on-disk state-root divergence, since the final canonical acceptance decision is still made by the fresh-reload path. This matches "temporary tip disagreement" / minority-triggerable validation divergence rather than a full chain split.

### Likelihood Explanation
Any single, unprivileged relayer/miner can trigger this merely by broadcasting a legitimately-signed Nakamoto block shortly after a chainstate event that changes a reward cycle's signer set contents (a same-fork rewrite of `.signers`/reward-set data for a cycle already cached, or simply timing skew across nodes' independent `refresh_reward_cycles` polling) — no majority or validator collusion is required, matching the report's "minority-triggerable" and "no external role/admin key" criteria. The condition depends on timing/ordering of a node's own refresh loop versus block arrival, so it is not deterministic per block, but it is reachable by an ordinary user submitting blocks/tenure activity without any special permission.

### Recommendation
- Do not gate signature-validity decisions used for peer-facing accept/relay behavior on the free-running `current_reward_sets` cache; instead, resolve the reward set the same way the authoritative append path does — freshly, per fork/parent, at the moment of the check — or explicitly document/enforce that a negative/positive verdict from this fast path is advisory-only and never causes divergent persistent state.
- Extend `check_reload_cached_reward_set`'s Epoch-3.x invalidation condition to also trigger on any change to the underlying `.signers` state for the cached cycle (not only reorgs), e.g. by keying the cache to the calculation block (as `contrib/stacks-inspect`'s `CachedRewardSet`/`load_reward_set_cached` already does for its own cache) [7](#0-6) , rather than trusting a coarse reorg-only signal.

### Proof of Concept
1. Node A and Node B are both tracking reward cycle `rc` with `current_reward_sets[rc]` populated from an earlier `.signers` write for that cycle.
2. Node A's `refresh_burnchain_view`/`refresh_reward_cycles` has not yet re-run since a later, same-fork event changed the effective signer weights/keys for `rc` (no reorg occurred, so `check_reload_cached_reward_set` returns `false` and the stale entry is kept) [8](#0-7) ; Node B has already refreshed and holds the updated reward set.
3. An unprivileged user broadcasts a Nakamoto block whose signer signatures are valid against the *updated* reward set but not the *stale* one (or vice versa).
4. `check_nakamoto_block_signer_signature` on Node A returns a different boolean than the equivalent check on Node B [9](#0-8) , so the two nodes disagree on whether to accept/relay the block at the p2p layer, even though both are honest and non-Byzantine.

### Citations

**File:** stackslib/src/net/unsolicited.rs (L247-269)
```rust
    pub(crate) fn check_nakamoto_block_signer_signature(
        &mut self,
        reward_cycle: u64,
        epoch_id: StacksEpochId,
        nakamoto_block: &NakamotoBlock,
    ) -> bool {
        let Some(rc_data) = self.current_reward_sets.get(&reward_cycle) else {
            info!(
                "{:?}: Failed to validate Nakamoto block {}/{}: no reward set for cycle {reward_cycle}",
                self.get_local_peer(),
                &nakamoto_block.header.consensus_hash,
                &nakamoto_block.header.block_hash(),
            );
            return false;
        };
        let Some(reward_set) = rc_data.reward_set() else {
            info!(
                "{:?}: No reward set for reward cycle {}",
                self.get_local_peer(),
                reward_cycle
            );
            return false;
        };
```

**File:** stackslib/src/net/unsolicited.rs (L271-280)
```rust
        if let Err(e) = nakamoto_block
            .header
            .verify_signer_signatures(reward_set, epoch_id)
        {
            info!(
                "{:?}: signature verification failure for Nakamoto block {}/{} in reward cycle {}: {:?}", self.get_local_peer(), &nakamoto_block.header.consensus_hash, &nakamoto_block.header.block_hash(), reward_cycle, &e
            );
            return false;
        }
        true
```

**File:** stackslib/src/net/p2p.rs (L4620-4641)
```rust
    fn check_reload_cached_reward_set(
        &self,
        sortdb: &SortitionDB,
        chainstate: &StacksChainState,
        rc: u64,
        tip_sn: &BlockSnapshot,
        tip_block_id: &StacksBlockId,
        tip_height: u64,
    ) -> Result<bool, net_error> {
        let epoch = self.get_epoch_at_burn_height(tip_sn.block_height);
        if epoch.epoch_id >= StacksEpochId::Epoch30 {
            // epoch 3, where there are no forks except from bugs or burnchain reorgs.
            // invalidate reward cycles on burnchain or stacks reorg, should they ever happen
            let reorg = Self::is_reorg(Some(&self.burnchain_tip), tip_sn, sortdb)
                || Self::is_nakamoto_reorg(
                    &self.stacks_tip.block_id(),
                    self.stacks_tip.height,
                    tip_block_id,
                    tip_height,
                    chainstate,
                );
            return Ok(reorg);
```

**File:** stackslib/src/net/p2p.rs (L4663-4676)
```rust
            if let Some(cached_rc_info) = self.current_reward_sets.get(&rc) {
                if let Some(anchor_hash) = anchor_hash_opt.as_ref() {
                    // careful -- the sortition DB stores a StacksBlockId's value (the tenure-start
                    // StacksBlockId) as a BlockHeaderHash, since that's what it was designed to
                    // deal with in the pre-Nakamoto days
                    if cached_rc_info.anchor_block_id() == StacksBlockId(anchor_hash.0)
                        || cached_rc_info.anchor_block_hash == *anchor_hash
                    {
                        // cached reward set data is still valid
                        test_debug!("Cached reward cycle {rc} is still valid");
                        return Ok(false);
                    }
                }
            }
```

**File:** stackslib/src/net/relay.rs (L676-723)
```rust
            let reward_cycle_info = if let Some(rc_info) = loaded_reward_sets.get(&sn_rc) {
                rc_info
            } else {
                let Some((reward_set_info, _)) = load_nakamoto_reward_set(
                    sn_rc,
                    &tip_sn.sortition_id,
                    burnchain,
                    chainstate,
                    stacks_tip,
                    sortdb,
                    &OnChainRewardSetProvider::new(),
                )
                .map_err(|e| {
                    error!(
                        "Failed to load reward cycle info for cycle {}: {:?}",
                        sn_rc, &e
                    );
                    match e {
                        CoordinatorError::ChainstateError(e) => {
                            error!(
                                "No RewardCycleInfo loaded for tip {}: {:?}",
                                &sn.consensus_hash, &e
                            );
                            net_error::ChainstateError(format!("{:?}", &e))
                        }
                        CoordinatorError::DBError(e) => {
                            error!(
                                "No RewardCycleInfo loaded for tip {}: {:?}",
                                &sn.consensus_hash, &e
                            );
                            net_error::DBError(e)
                        }
                        _ => {
                            error!(
                                "Failed to load RewardCycleInfo for tip {}: {:?}",
                                &sn.consensus_hash, &e
                            );
                            net_error::NoPoXRewardSet(sn_rc)
                        }
                    }
                })?
                else {
                    error!("No reward set for reward cycle {}", &sn_rc);
                    return Err(net_error::NoPoXRewardSet(sn_rc));
                };

                loaded_reward_sets.insert(sn_rc, reward_set_info);
                loaded_reward_sets.get(&sn_rc).expect("FATAL: infallible")
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2327-2354)
```rust
        let elected_height = sort_db
            .get_consensus_hash_height(&next_ready_block.header.consensus_hash)?
            .ok_or_else(|| ChainstateError::NoSuchBlockError)?;
        let elected_in_cycle = sort_db
            .pox_constants
            .block_height_to_reward_cycle(sort_db.first_block_height, elected_height)
            .ok_or_else(|| {
                ChainstateError::InvalidStacksBlock(
                    "Elected in block height before first_block_height".into(),
                )
            })?;
        let active_reward_set = OnChainRewardSetProvider::<DummyEventDispatcher>(None).read_reward_set_nakamoto_of_cycle(
            elected_in_cycle,
            stacks_chain_state,
            sort_db,
            &next_ready_block.header.parent_block_id,
            true,
        ).map_err(|e| {
            warn!(
                "Cannot process Nakamoto block: could not load reward set that elected the block";
                "err" => ?e,
                "consensus_hash" => %next_ready_block.header.consensus_hash,
                "stacks_block_hash" => %next_ready_block.header.block_hash(),
                "stacks_block_id" => %next_ready_block.header.block_id(),
                "parent_block_id" => %next_ready_block.header.parent_block_id,
            );
            ChainstateError::NoSuchBlockError
        })?;
```

**File:** contrib/stacks-inspect/src/lib.rs (L1016-1032)
```rust
fn load_reward_set_cached<'a>(
    cycle: u64,
    reward_set_cache: &'a mut HashMap<u64, CachedRewardSet>,
    stacks_chain_state: &mut StacksChainState,
    sort_db: &SortitionDB,
    parent_block_id: &StacksBlockId,
) -> Result<&'a RewardSet, String> {
    let cached_ok = match reward_set_cache.get(&cycle) {
        Some(entry) => NakamotoChainState::get_header_by_coinbase_height(
            &mut stacks_chain_state.index_conn(),
            parent_block_id,
            entry.calc_coinbase_height,
        )
        .map_err(|e| format!("Failed to resolve cached calculation block: {e:?}"))?
        .is_some_and(|hdr| hdr.index_block_hash() == entry.calc_block_id),
        None => false,
    };
```
