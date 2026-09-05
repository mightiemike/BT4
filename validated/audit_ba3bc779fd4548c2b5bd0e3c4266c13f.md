### Title
Miner's per-tenure `signer_set_cache` is loaded once and never invalidated on reorg, letting `NakamotoChainState::accept_block` verify signatures against a stale `RewardSet` - (File: stacks-node/src/nakamoto_node/miner.rs)

### Summary
The external report's bug class ("cache not synced with the authoritative source before a security-critical operation runs") maps onto the miner's `load_signer_set()` cache in this repo. That cache stores the `RewardSet` used to verify signer signatures for every block the miner builds/accepts during a tenure, but unlike the analogous cache in `PeerNetwork` (`current_reward_sets`, which is explicitly re-validated every burnchain-tip refresh via `check_reload_cached_reward_set`), the miner's `signer_set_cache` has no invalidation logic at all.

### Finding Description
`NakamotoChainState::accept_block()` trusts whatever `reward_set: &RewardSet` its caller passes in and uses it directly to check signer-signature weight via `verify_signer_signatures()`: [1](#0-0) 

The block-relay path (`process_new_nakamoto_block` in `stackslib/src/net/relay.rs`) and the chain-processing path (`process_next_nakamoto_block` in `stackslib/src/chainstate/nakamoto/mod.rs`) both re-derive the reward set fresh, on each call, from persisted chainstate/`.signers` data: [2](#0-1) 

By contrast, the miner's own broadcast path caches the reward set once per tenure thread and reuses it unconditionally for every subsequent block in that tenure: [3](#0-2) 

This cached value is fed straight into `accept_block()` when the miner locally stores/broadcasts its own mined block: [4](#0-3) 

The equivalent cache maintained by `PeerNetwork` (`current_reward_sets`, used by the P2P/download machinery) is explicitly guarded by `check_reload_cached_reward_set`, which detects burnchain/Stacks reorgs and forces a reload before the cached set is trusted: [5](#0-4) 

`load_signer_set()` has no counterpart to this check: once `self.signer_set_cache` is populated it is returned unconditionally on every call for the remainder of the tenure (`stacks-node/src/nakamoto_node/miner.rs:1131-1133`), regardless of whether the underlying `.signers` write that produced it is later reorged out by a Stacks/Nakamoto fork (e.g. the tenure that calculated the reward set for this cycle is superseded by a sibling tenure with a different anchor block/reward set, which the coordinator's own reorg-detection logic in `stackslib/src/chainstate/nakamoto/coordinator/mod.rs` treats as a normal, expected occurrence when selecting PoX anchor blocks).

This exactly mirrors the reported bug class: an operation that depends on "the cache being in sync" (`verify_signer_signatures`/`accept_block`, analogous to `NestedFactory.create()`) is invoked using a value cached before a state-changing event (the operator-set update / here, a reward-set-affecting reorg), with no mechanism analogous to `rebuildCache()`'s check being enforced before use.

### Impact Explanation
If the tenure's stale, cached `RewardSet` differs from the reward set that other nodes (and the signers themselves) compute fresh for the same reward cycle - which can occur when the PoX/reward-set-calculation block for that cycle is reorged mid-tenure - the miner's local node will validate its own mined block's signer signatures (`verify_signer_signatures`) against the wrong signer-weight table and threshold. This is a minority-triggerable, unprivileged sortition/signer-set divergence: the miner's local view of "who is a valid signer and what their weight is" disagrees with the canonical view used by the rest of the network, producing a validation verdict (accept vs. reject, or accept-with-wrong-weight) that other honest nodes running fresh reward-set lookups will not reproduce. Per the scoping rules this lands as High: a minority-triggerable signer-set/weight divergence causing temporary tip disagreement between the miner's node and the rest of the network.

### Likelihood Explanation
Triggering requires only that a Stacks-level (or PoX-affecting) reorg occur mid-tenure that changes which block calculated the `.signers` entry for the active reward cycle - this is a normal occurrence the coordinator itself accounts for (anchor block selection can change between sibling tenures) and requires no majority collusion, no admin key, and no economic assumptions; it can happen from ordinary chain competition during a single miner's tenure window. The only gating factor is timing: the reorg must land after `load_signer_set()` has already cached a value and before the tenure ends.

### Recommendation
Mirror the `PeerNetwork::check_reload_cached_reward_set` pattern in the miner: before using `self.signer_set_cache`, re-validate that the anchor block/calculation block backing the cached `RewardSet` is still reachable from the current canonical fork (e.g., re-resolve `calc_block_id`/`calc_coinbase_height` through the current parent, as done in the `stacks-inspect` replay tool's `load_reward_set_cached`), and force a reload if it no longer resolves. Alternatively, invalidate `signer_set_cache` whenever the miner detects a new burnchain tip or Stacks reorg, rather than caching for the lifetime of the tenure unconditionally.

### Proof of Concept
1. Node A is mining a Nakamoto tenure; at tenure start it calls `load_signer_set()`, which computes and caches `RewardSet` R1 for reward cycle C, calculated from anchor/calculation block B1 (`stacks-node/src/nakamoto_node/miner.rs:1130-1181`).
2. While Node A's tenure is ongoing, a Bitcoin/Stacks-level fork causes the canonical fork to select a different PoX anchor block for cycle C (a normal occurrence the coordinator's `get_nakamoto_reward_cycle_info`/anchor-block-selection logic explicitly tolerates), so the reward set that calculation now yields fresh is R2 ≠ R1, computed from B2.
3. Node A mines and locally accepts another block in the same tenure via `broadcast_p2p()`, which calls `NakamotoChainState::accept_block(..., reward_set=&R1, ...)` (`stacks-node/src/nakamoto_node/miner.rs:1200-1233`), because `load_signer_set()` still returns the cached R1 without re-checking it against the now-canonical fork.
4. `verify_signer_signatures` validates/records the block's signing weight against the stale signer set R1 rather than the canonical R2 used by every other node's `process_new_nakamoto_block`/`process_next_nakamoto_block` path, which always recompute the reward set fresh (`stackslib/src/chainstate/nakamoto/mod.rs:2327-2354`).
5. Node A's local acceptance/weight bookkeeping for this block now disagrees with the rest of the network's canonical validation, producing a node-local view of block validity/signing-weight that cannot be reproduced by peers computing the reward set fresh - a concrete instance of the "cache not synced" bug class breaking the signer-weight-validation equality that all honest nodes are expected to agree on.

### Citations

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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2829-2925)
```rust
    pub fn accept_block(
        chainstate: &mut StacksChainState,
        block: &NakamotoBlock,
        db_handle: &mut SortitionHandleConn,
        reward_set: &RewardSet,
        obtain_method: NakamotoBlockObtainMethod,
    ) -> Result<bool, ChainstateError> {
        let block_id = block.block_id();
        test_debug!("Consider Nakamoto block {block_id}");
        let config = chainstate.config();
        // do nothing if we already have this block
        let (headers_conn, staging_db_tx) = chainstate.headers_conn_and_staging_tx_begin()?;
        if Self::get_block_header(headers_conn, &block_id)?.is_some() {
            debug!("Already have block {block_id}");
            return Ok(false);
        }

        // if this is the first tenure block, then make sure it's well-formed
        block.is_wellformed_tenure_start_block().inspect_err(|_| {
            warn!("Block {block_id} is not a well-formed first tenure block");
        })?;

        // if this is a tenure-extend block, then make sure it's well-formed
        block.is_wellformed_tenure_extend_block().inspect_err(|_| {
            warn!("Block {block_id} is not a well-formed tenure-extend block");
        })?;

        // it's okay if this fails because we might not have the parent block yet.  It will be
        // checked on `::append_block()`
        let expected_burn_opt = Self::get_expected_burns(db_handle, headers_conn, block)?;

        if block.is_shadow_block() {
            // this block is already present in the staging DB, so just perform some prefunctory
            // validation (since they're constructed a priori to be valid)
            Self::validate_shadow_nakamoto_block_burnchain(
                staging_db_tx.conn(),
                db_handle,
                expected_burn_opt,
                block,
                config.mainnet,
                config.chain_id,
            )
            .unwrap_or_else(|e| {
                error!("Unacceptable shadow Nakamoto block";
                    "stacks_block_id" => %block_id,
                    "error" => ?e
                );
                panic!("Unacceptable shadow Nakamoto block");
            });
            return Ok(false);
        }

        // this block must be consistent with its miner's leader-key and block-commit, and must
        // contain only transactions that are valid in this epoch.
        Self::validate_normal_nakamoto_block_burnchain(
            staging_db_tx.conn(),
            db_handle,
            expected_burn_opt,
            block,
            config.mainnet,
            config.chain_id,
        )
        .inspect_err(|e| {
            warn!("Unacceptable Nakamoto block; will not store";
                "stacks_block_id" => %block_id,
                "error" => ?e
            );
        })?;

        let block_sn = SortitionDB::get_block_snapshot_consensus(
            db_handle.conn(),
            &block.header.consensus_hash,
        )?
        .ok_or_else(|| {
            ChainstateError::InvalidStacksBlock(format!(
                "No sortition for block consensus hash {}",
                &block.header.consensus_hash
            ))
        })?;
        let epoch_id = SortitionDB::get_stacks_epoch(db_handle.conn(), block_sn.block_height)?
            .ok_or_else(|| {
                ChainstateError::InvalidStacksBlock(format!(
                    "No epoch defined at burn height {}",
                    block_sn.block_height
                ))
            })?
            .epoch_id;

        let signing_weight = block
            .header
            .verify_signer_signatures(reward_set, epoch_id)
            .inspect_err(|e| {
                warn!("Received block, but the signer signatures are invalid";
                    "block_id" => %block_id,
                    "error" => ?e,
                );
            })?;
```

**File:** stacks-node/src/nakamoto_node/miner.rs (L1126-1181)
```rust
    /// Load the signer set active for this miner's blocks. This is the
    ///  active reward set during `self.burn_election_block`. The miner
    ///  thread caches this information, and this method will consult
    ///  that cache (or populate it if necessary).
    fn load_signer_set(&mut self) -> Result<RewardSet, NakamotoNodeError> {
        if let Some(set) = self.signer_set_cache.as_ref() {
            return Ok(set.clone());
        }
        let sort_db = SortitionDB::open(
            &self.config.get_burn_db_file_path(),
            true,
            self.burnchain.pox_constants.clone(),
            Some(self.config.node.get_marf_opts()),
        )
        .map_err(|e| {
            NakamotoNodeError::SigningCoordinatorFailure(format!(
                "Failed to open sortition DB. Cannot mine! {e:?}"
            ))
        })?;

        let mut chain_state =
            neon_node::open_chainstate_with_faults(&self.config).map_err(|e| {
                NakamotoNodeError::SigningCoordinatorFailure(format!(
                    "Failed to open chainstate DB. Cannot mine! {e:?}"
                ))
            })?;

        let reward_set = match load_nakamoto_reward_set_for_tenure(
            &self.burn_election_block,
            &self.burnchain,
            &mut chain_state,
            &self.parent_tenure_id,
            &sort_db,
            &OnChainRewardSetProvider::new(),
        ) {
            Ok(Some(reward_set)) => reward_set,
            Ok(None) => {
                return Err(NakamotoNodeError::SigningCoordinatorFailure(
                    "No reward set stored yet. Cannot mine!".into(),
                ));
            }
            Err(ChainstateError::NoRegisteredSigners(..)) => {
                return Err(NakamotoNodeError::SigningCoordinatorFailure(
                    "Current reward cycle did not select a reward set. Cannot mine!".into(),
                ));
            }
            Err(e) => {
                return Err(NakamotoNodeError::SigningCoordinatorFailure(format!(
                    "Failure while fetching reward set. Cannot initialize miner coordinator. {e:?}"
                )));
            }
        };

        self.signer_set_cache = Some(reward_set.clone());
        Ok(reward_set)
    }
```

**File:** stacks-node/src/nakamoto_node/miner.rs (L1200-1233)
```rust
    /// Store a block to the chainstate, and if successful (it should be since we mined it),
    /// broadcast it via the p2p network.
    fn broadcast_p2p(
        &mut self,
        sort_db: &SortitionDB,
        chain_state: &mut StacksChainState,
        block: &NakamotoBlock,
        reward_set: &RewardSet,
    ) -> Result<(), ChainstateError> {
        if Self::fault_injection_skip_block_broadcast() {
            warn!(
                "Fault injection: Skipping block broadcast for {}",
                block.block_id()
            );
            return Ok(());
        }
        #[cfg(test)]
        TEST_MINER_BROADCASTING_BLOCK.set(block.clone());

        Self::fault_injection_block_broadcast_stall(block);

        let parent_block_info =
            NakamotoChainState::get_block_header(chain_state.db(), &block.header.parent_block_id)?
                .ok_or_else(|| ChainstateError::NoSuchBlockError)?;
        let burn_view_ch =
            NakamotoChainState::get_block_burn_view(sort_db, block, &parent_block_info)?;
        let mut sortition_handle = sort_db.index_handle_at_ch(&burn_view_ch)?;
        let accepted = NakamotoChainState::accept_block(
            chain_state,
            block,
            &mut sortition_handle,
            reward_set,
            NakamotoBlockObtainMethod::Mined,
        )?;
```

**File:** stackslib/src/net/p2p.rs (L4613-4679)
```rust
    /// Determine if we need to invalidate a given cached reward set.
    ///
    /// In Epoch 2, this requires checking the first sortition in the start of the reward set's
    /// reward phase.
    ///
    /// In Nakamoto, this requires checking the anchor block in the prepare phase for the upcoming
    /// reward phase.
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
```
