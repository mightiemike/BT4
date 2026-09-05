# No vulnerability found for this question.

**Analysis supporting this conclusion:**

The claimed equality break — `reward_set_used_for_verification == reward_set_committed_by_.signers_for_that_cycle` — does not actually break under the described scenario.

The reward set for reward_cycle N is only written once per fork by `check_and_handle_prepare_phase_start`, which explicitly guards against duplicate writes via the `SIGNERS_UPDATE_STATE` check: it only recomputes if `cycle_number < cycle_of_prepare_phase` [1](#0-0) . Once `.signers` records `cycle-set-height` for cycle N on a given fork, that mapping is immutable for that fork — there is no "late update" that overwrites an already-set cycle within the same fork.

`OnChainRewardSetProvider::read_reward_set_nakamoto_of_cycle` resolves the reward set deterministically by looking up `cycle-set-height` in `.signers` at the requested `block_id`, then reading the reward set stored at that specific block [2](#0-1) . If `.signers` hasn't been updated yet for cycle N, this returns `Err(Error::PoXAnchorBlockRequired)` rather than a partial/stale value [3](#0-2) . So `current_reward_sets` in `PeerNetwork` can never be populated with a "not yet finalized" reward set for cycle N — it either gets the finalized value, or the load fails and no cache entry is created, at which point `check_nakamoto_block_signer_signature` explicitly rejects the block via the "no reward set for cycle" path rather than accepting it [4](#0-3) .

The only way the reward set for a given `(reward_cycle, tip)` pair could legitimately change is via a fork/reorg, and `refresh_reward_cycles`/`check_reload_cached_reward_set` explicitly detects this in the Nakamoto epoch by checking `is_reorg`/`is_nakamoto_reorg` against the cached `burnchain_tip`/`stacks_tip`, forcing a reload when a reorg is detected [5](#0-4) . This guard covers exactly the divergence scenario the question describes.

Thus there is no reachable "stale vs. actual" divergence for the same fork and same reward cycle: the `.signers` write is a one-time, guarded, per-fork computation, the cache-population path fails safe when the write hasn't happened yet, and reorg-driven changes are already invalidated by existing cache-reload logic.

### Citations

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L1001-1014)
```rust
                let value = clarity_db.lookup_variable_unknown_descriptor(
                    signers_contract,
                    SIGNERS_UPDATE_STATE,
                    &current_epoch,
                )?;
                let cycle_number = value.expect_u128().map_err(|_| {
                    ChainstateError::Expects(format!(
                        "Expected u128 for .signers {SIGNERS_UPDATE_STATE} variable"
                    ))
                })?;
                // if the cycle_number is less than `cycle_of_prepare_phase`, we need to update
                //  the .signers state.
                let needs_update = cycle_number < u128::from(cycle_of_prepare_phase);
                Ok(needs_update)
```

**File:** stackslib/src/chainstate/nakamoto/coordinator/mod.rs (L98-143)
```rust
    pub fn read_reward_set_nakamoto_of_cycle(
        &self,
        cycle: u64,
        chainstate: &mut StacksChainState,
        sortdb: &SortitionDB,
        block_id: &StacksBlockId,
        debug_log: bool,
    ) -> Result<RewardSet, Error> {
        // figure out the block in which .signers was last updated for this cycle
        let Some(coinbase_height_of_calculation) = chainstate
            .eval_boot_code_read_only(
                sortdb,
                block_id,
                SIGNERS_NAME,
                &format!("(map-get? cycle-set-height u{cycle})"),
            )?
            .expect_optional()
            .map_err(|_| {
                ChainstateError::Expects(format!(
                    "(map-get? cycle-set-height u{cycle}) did not return an optional"
                ))
            })?
            .map(|x| {
                let as_u128 = x.expect_u128().map_err(|_| {
                    ChainstateError::Expects("cycle-set-height did not return a u128".into())
                })?;
                u64::try_from(as_u128)
                    .map_err(|_| ChainstateError::Expects("block height exceeded u64".into()))
            })
            .transpose()?
        else {
            err_or_debug!(
                debug_log,
                "The reward set was not written to .signers before it was needed by Nakamoto";
                "cycle_number" => cycle,
            );
            return Err(Error::PoXAnchorBlockRequired);
        };

        self.read_reward_set_at_calculated_block(
            coinbase_height_of_calculation,
            chainstate,
            block_id,
            debug_log,
        )
    }
```

**File:** stackslib/src/net/unsolicited.rs (L253-269)
```rust
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

**File:** stackslib/src/net/p2p.rs (L4630-4680)
```rust
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
        } else {
            // epoch 2
            // NOTE: + 1 needed because the sortition db indexes anchor blocks at index height 1,
            // not 0
            let ih = sortdb.index_handle(&tip_sn.sortition_id);
            let rc_start_height = self.burnchain.nakamoto_first_block_of_cycle(rc) + 1;
            let Some(ancestor_sort_id) =
                get_ancestor_sort_id(&ih, rc_start_height, &tip_sn.sortition_id)?
            else {
                // reward cycle is too far back for there to be an ancestor, so no need to
                // reload
                test_debug!(
                    "No ancestor sortition ID off of {} (height {}) at {rc_start_height})",
                    &tip_sn.sortition_id,
                    tip_sn.block_height
                );
                return Ok(false);
            };
            let ancestor_ih = sortdb.index_handle(&ancestor_sort_id);
            let anchor_hash_opt = ancestor_ih.get_last_anchor_block_hash()?;

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
        }

        Ok(true)
    }
```
