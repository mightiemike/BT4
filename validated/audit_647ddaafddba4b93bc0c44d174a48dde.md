### Title
Nakamoto block timestamp monotonicity is enforced only by the signer's soft pre-signing validation, not by node-level block acceptance (`append_block`) - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
The rule that a Nakamoto block's `header.timestamp` must be strictly greater than its parent's timestamp and no more than 15 seconds in the future is checked only inside the block-proposal RPC handler used by signers before they sign a block [1](#0-0) . This same invariant is never re-checked by the consensus-critical path that actually appends a block to chainstate (`NakamotoChainState::append_block` / `validate_normal_nakamoto_block_burnchain` / `accept_block`), which instead accepts any block whose signature/signer-signature set is valid, irrespective of its timestamp value relative to its parent [2](#0-1) [3](#0-2) .

### Finding Description
`NakamotoBlockHeader::timestamp` is documented as needing to be "Greater than the timestamp of its parent block" and "At most 15 seconds into the future" for signers to consider the block valid [4](#0-3) . This exact check is implemented only in `NakamotoBlockProposal::validate()`, the RPC endpoint that signers call before countersigning a proposed block; comments there explicitly frame prior checks as "(For the signer)" [5](#0-4) .

The shared function `validate_normal_nakamoto_block_burnchain`, which is invoked both from this RPC path and from the node's actual block-acceptance path (`accept_block`), does not perform this timestamp comparison — it only validates burn-chain attachment, miner signature, tenure tx, and coinbase/VRF proof [2](#0-1) [6](#0-5) . Likewise, `accept_block` and `append_block`, which are the functions all nodes run to admit a block into their chainstate, check parent linkage, chain length, sortition existence, tenure continuity, and burn totals, but contain no comparison against `NakamotoBlockHeader::timestamp` at all [7](#0-6) [8](#0-7) .

This mirrors the geth bug class: a time-derived value (block timestamp) is used to gate certain decisions (which block signers will sign, tenure-extension eligibility windows enforced client-side in the miner via `validate_timestamp_info`) [9](#0-8) , but the core state-transition function that all replicas run to determine the canonical chain does not independently enforce the same timestamp equality/ordering invariant. Because the field is part of the signed header, an honest signer set that runs its own timestamp check will reject an out-of-order/future-dated block, but a node whose chain-following logic only calls `append_block` (e.g., replaying blocks obtained via P2P/relay rather than proposal RPC, or during a mass block replay/reorg) admits the block purely on signature-threshold validity.

### Impact Explanation
If a miner obtains signer-threshold approval for two competing/sibling blocks for the same tenure slot (or replays a previously signed block with a backdated/future timestamp through a path that bypasses `postblock_proposal::validate`), `append_block` will accept it without detecting the timestamp anomaly. Since downstream logic (tenure-extend timing, minimum time between blocks, `validate_timestamp_info` in the miner) relies on this field being monotonic and bounded, an attacker able to route an already-signed but timestamp-invalid block through the block-acceptance path (rather than the proposal-validation path) can create temporary tip/timestamp disagreement between nodes that revalidate blocks (which apply the RPC check) and nodes that merely append pushed/relayed blocks (which don't). This is bounded to a High-severity "temporary tip disagreement / static-validation divergence" rather than an unconditional chain split, since acceptance still requires a valid threshold signer signature set.

### Likelihood Explanation
Likelihood is limited by the fact that signers, when asked to sign a proposal via the intended flow, do enforce the timestamp rule and thus a maliciously-timestamped block cannot normally be freshly signed. The exposure specifically arises for any code path where an already-signed block is appended without going back through `postblock_proposal::validate` (e.g., a node processing blocks received via block push/relay, replay after downtime, or a signer bug). I was not able to fully verify inside the available index whether `validate_header_static` (in `stackslib/src/chainstate/nakamoto/mod.rs`) performs any independent timestamp bound check, since I could only confirm its existence and outer structure, not its full body — this is a gap in my verification and should be checked directly in the source before treating this as conclusively exploitable.

### Recommendation
Move the timestamp monotonicity and future-bound checks out of the signer-only RPC handler (`postblock_proposal.rs`) and into `validate_normal_nakamoto_block_burnchain` or directly into `NakamotoChainState::append_block`, so that every node enforces the same timestamp invariant as a hard consensus rule when appending any Nakamoto block, regardless of the path (proposal RPC, P2P relay, or replay) by which the block was obtained.

### Proof of Concept
Not independently reproduced in this repo-only analysis; the control-flow evidence is: (1) the timestamp check exists only at [1](#0-0) , (2) the shared burnchain-validation function called from both the RPC path and `accept_block` contains no such check [2](#0-1) , and (3) `append_block`'s attachment/continuity checks likewise omit it [8](#0-7) . Confirming actual exploitability would require tracing the exact node code path used when accepting relayed/pushed Nakamoto blocks (outside the `postblock_proposal` RPC) to verify no other call site performs this check before `append_block` is invoked — this could not be fully confirmed with the available tools within the current session.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L600-669)
```rust
        // (For the signer)
        // Verify that the block's tenure is on the canonical sortition history
        Self::check_block_has_valid_tenure(&db_handle, &self.block.header.consensus_hash)?;

        // (For the signer)
        // Verify that this block's parent is the highest such block we can build off of
        Self::check_block_has_valid_parent(chainstate, sortdb, &self.block)?;

        // get the burnchain tokens spent for this block. There must be a record of this (i.e.
        // there must be a block-commit for this), or otherwise this block doesn't correspond to
        // any burnchain chainstate.
        let expected_burn_opt =
            NakamotoChainState::get_expected_burns(&db_handle, chainstate.db(), &self.block)?;
        if expected_burn_opt.is_none() {
            warn!(
                "Rejected block proposal";
                "reason" => "Failed to find parent expected burns",
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::UnknownParent,
                reason: "Failed to find parent expected burns".into(),
                failed_txid: None,
            });
        };

        // Static validation checks
        NakamotoChainState::validate_normal_nakamoto_block_burnchain(
            chainstate.nakamoto_blocks_db(),
            &db_handle,
            expected_burn_opt,
            &self.block,
            mainnet,
            self.chain_id,
        )?;

        // Validate txs against chainstate

        // Validate the block's timestamp. It must be:
        // - Greater than the parent block's timestamp
        // - At most 15 seconds into the future
        if let StacksBlockHeaderTypes::Nakamoto(parent_nakamoto_header) =
            &parent_stacks_header.anchored_header
        {
            if self.block.header.timestamp <= parent_nakamoto_header.timestamp {
                warn!(
                    "Rejected block proposal";
                    "reason" => "Block timestamp is not greater than parent block",
                    "block_timestamp" => self.block.header.timestamp,
                    "parent_block_timestamp" => parent_nakamoto_header.timestamp,
                );
                return Err(BlockValidateRejectReason {
                    reason_code: ValidateRejectCode::InvalidTimestamp,
                    reason: "Block timestamp is not greater than parent block".into(),
                    failed_txid: None,
                });
            }
        }
        if self.block.header.timestamp > get_epoch_time_secs() + 15 {
            warn!(
                "Rejected block proposal";
                "reason" => "Block timestamp is too far into the future",
                "block_timestamp" => self.block.header.timestamp,
                "current_time" => get_epoch_time_secs(),
            );
            return Err(BlockValidateRejectReason {
                reason_code: ValidateRejectCode::InvalidTimestamp,
                reason: "Block timestamp is too far into the future".into(),
                failed_txid: None,
            });
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L767-771)
```rust
    /// A Unix time timestamp of when this block was mined, according to the miner.
    /// For the signers to consider a block valid, this timestamp must be:
    ///  * Greater than the timestamp of its parent block
    ///  * At most 15 seconds into the future
    pub timestamp: u64,
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1917-1937)
```rust
    fn validate_normal_against_burnchain(
        &self,
        tenure_burn_chain_tip: &BlockSnapshot,
        expected_burn: Option<u64>,
        miner_pubkey_hash160: &Hash160,
        vrf_public_key: &VRFPublicKey,
    ) -> Result<(), ChainstateError> {
        self.common_validate_against_burnchain(tenure_burn_chain_tip, expected_burn)?;
        self.check_miner_signature(miner_pubkey_hash160)?;
        self.check_tenure_tx()?;
        self.check_normal_coinbase_tx(vrf_public_key, &tenure_burn_chain_tip.sortition_hash)?;

        // not verified by this method:
        // * chain_length       (need parent block header)
        // * parent_block_id    (need parent block header)
        // * block-commit seed  (need parent block)
        // * tx_merkle_root     (already verified; validated on deserialization)
        // * state_index_root   (validated on process_block())
        // * stacker signature  (validated on accept_block())
        Ok(())
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2739-2817)
```rust
    pub(crate) fn validate_normal_nakamoto_block_burnchain(
        staging_db: NakamotoStagingBlocksConnRef,
        db_handle: &SortitionHandleConn,
        expected_burn: Option<u64>,
        block: &NakamotoBlock,
        mainnet: bool,
        chain_id: u32,
    ) -> Result<(), ChainstateError> {
        assert!(!block.is_shadow_block());

        let tenure_burn_chain_tip = Self::validate_nakamoto_tenure_snapshot(db_handle, block)?;

        // block-commit of this sortition
        let Some(block_commit) = db_handle.get_block_commit_by_txid(
            &tenure_burn_chain_tip.sortition_id,
            &tenure_burn_chain_tip.winning_block_txid,
        )?
        else {
            warn!(
                "No block commit for {} in sortition for {}",
                &tenure_burn_chain_tip.winning_block_txid, &block.header.consensus_hash
            );
            return Err(ChainstateError::InvalidStacksBlock(
                "No block-commit in sortition for block's consensus hash".into(),
            ));
        };

        // if the *parent* of this block is a shadow block, then the block-commit's
        // parent_vtxindex *MUST* be 0 and the parent_block_ptr *MUST* be the tenure of the
        // shadow block.
        //
        // if the parent is not a shadow block, then this is a no-op.
        Self::validate_shadow_parent_burnchain(staging_db, db_handle, block, &block_commit)?;

        // key register of the winning miner
        let leader_key = db_handle
            .get_leader_key_at(
                u64::from(block_commit.key_block_ptr),
                u32::from(block_commit.key_vtxindex),
            )?
            .expect("FATAL: have block commit but no leader key");

        // miner key hash160.
        let miner_pubkey_hash160 = leader_key
            .interpret_nakamoto_signing_key()
            .ok_or(ChainstateError::NoSuchBlockError)
            .inspect_err(|_e| {
                warn!(
                    "Leader key did not contain a hash160 of the miner signing public key";
                    "leader_key" => ?leader_key,
                );
            })?;

        // attaches to burn chain
        if let Err(e) = block.validate_normal_against_burnchain(
            &tenure_burn_chain_tip,
            expected_burn,
            &miner_pubkey_hash160,
            &leader_key.public_key,
        ) {
            warn!(
                "Invalid Nakamoto block, could not validate on burnchain";
                "consensus_hash" => %block.header.consensus_hash,
                "stacks_block_hash" => %block.header.block_hash(),
                "error" => ?e
            );

            return Err(e);
        }

        Self::validate_nakamoto_block_static(
            mainnet,
            chain_id,
            db_handle.deref(),
            block,
            tenure_burn_chain_tip.block_height,
        )?;
        Ok(())
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2829-2916)
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

```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L5026-5150)
```rust
    /// Append a Nakamoto Stacks block to the Stacks chain state.
    /// NOTE: This does _not_ set the block as processed!  The caller must do this.
    pub fn append_block<'a>(
        chainstate_tx: &mut ChainstateTx,
        clarity_instance: &'a mut ClarityInstance,
        burn_dbconn: &mut SortitionHandleConn,
        burnchain_view: &ConsensusHash,
        pox_constants: &PoxConstants,
        parent_chain_tip: &StacksHeaderInfo,
        chain_tip_burn_header_hash: &BurnchainHeaderHash,
        chain_tip_burn_header_height: u32,
        chain_tip_burn_header_timestamp: u64,
        block: &NakamotoBlock,
        block_size: u64,
        burnchain_commit_burn: u64,
        burnchain_sortition_burn: u64,
        active_reward_set: &RewardSet,
        do_not_advance: bool,
    ) -> Result<
        (
            StacksEpochReceipt,
            PreCommitClarityBlock<'a>,
            Option<RewardSetData>,
            Vec<StacksTransactionEvent>,
        ),
        ChainstateError,
    > {
        debug!(
            "Process Nakamoto block {:?} with {} transactions",
            &block.header.block_hash().to_hex(),
            block.txs.len()
        );

        let next_block_height = block.header.chain_length;
        let first_block_height = burn_dbconn.context.first_block_height;

        // check that this block attaches to the `parent_chain_tip`
        let (parent_ch, parent_block_hash) = if block.is_first_mined() {
            (
                FIRST_BURNCHAIN_CONSENSUS_HASH.clone(),
                FIRST_STACKS_BLOCK_HASH.clone(),
            )
        } else {
            (
                parent_chain_tip.consensus_hash.clone(),
                parent_chain_tip.anchored_header.block_hash(),
            )
        };

        let parent_block_id = StacksBlockId::new(&parent_ch, &parent_block_hash);
        if parent_block_id != block.header.parent_block_id {
            warn!("Error processing nakamoto block: Parent consensus hash does not match db view";
                  "db.parent_block_id" => %parent_block_id,
                  "header.parent_block_id" => %block.header.parent_block_id);
            return Err(ChainstateError::InvalidStacksBlock(
                "Parent block does not match".into(),
            ));
        }

        if parent_chain_tip.stacks_block_height.saturating_add(1) != block.header.chain_length {
            warn!("Error processing nakamoto block: Parent height does not agree with block chain length";
                  "parent_chain_tip.block_height" => %parent_chain_tip.stacks_block_height,
                  "block.header.chain_length" => &block.header.chain_length);
            return Err(ChainstateError::InvalidStacksBlock(
                "Parent block height does not agree with child block".into(),
            ));
        }

        // look up this block's sortition's burnchain block hash and height.
        // It must exist in the same Bitcoin fork as our `burn_dbconn`.
        let tenure_block_snapshot =
            Self::check_sortition_exists(burn_dbconn, &block.header.consensus_hash)?;
        let block_hash = block.header.block_hash();

        let is_new_tenure = block.is_wellformed_tenure_start_block()?;
        // this block is mined in the ongoing tenure.
        if !is_new_tenure
            && !Self::check_tenure_continuity(chainstate_tx.as_tx(), &parent_ch, &block.header)?
        {
            // this block is not part of the ongoing tenure; it's invalid
            return Err(ChainstateError::ExpectedTenureChange);
        }
        let is_tenure_extend = block.is_wellformed_tenure_extend_block()?;
        if is_tenure_extend && is_new_tenure {
            return Err(ChainstateError::InvalidStacksBlock(
                "Both started and extended tenure".into(),
            ));
        }

        let tenure_cause = block
            .try_get_tenure_change_payload()
            .map(|payload| MinerTenureInfoCause::from(payload.cause))
            .unwrap_or(MinerTenureInfoCause::NoTenureChange);

        let parent_coinbase_height = if block.is_first_mined() {
            0
        } else {
            Self::get_coinbase_height_at(chainstate_tx.as_tx(), &parent_block_id)?.ok_or_else(
                || {
                    warn!(
                        "Parent of Nakamoto block is not in block headers DB yet";
                        "consensus_hash" => %block.header.consensus_hash,
                        "stacks_block_hash" => %block.header.block_hash(),
                        "stacks_block_id" => %block.header.block_id(),
                        "parent_block_hash" => %parent_block_hash,
                        "parent_block_id" => %parent_block_id
                    );
                    ChainstateError::NoSuchBlockError
                },
            )?
        };

        let expected_burn_opt = Self::get_expected_burns(burn_dbconn, chainstate_tx, block)
            .map_err(|e| {
                warn!("Unacceptable Nakamoto block: could not load expected burns (unable to find its paired sortition)";
                    "consensus_hash" => %block.header.consensus_hash,
                    "stacks_block_hash" => %block.header.block_hash(),
                    "stacks_block_id" => %block.block_id(),
                    "parent_block_id" => %block.header.parent_block_id,
                    "error" => e.to_string(),
                );
                ChainstateError::InvalidStacksBlock("Invalid Nakamoto block: could not find sortition burns".into())
            })?;

        let Some(expected_burn) = expected_burn_opt else {
```

**File:** stacks-node/src/nakamoto_node/miner.rs (L1564-1585)
```rust
    fn validate_timestamp_info(
        &self,
        current_timestamp_secs: u64,
        stacks_parent_header: &StacksHeaderInfo,
    ) -> bool {
        let parent_timestamp = match stacks_parent_header.anchored_header.as_stacks_nakamoto() {
            Some(naka_header) => naka_header.timestamp,
            None => stacks_parent_header.burn_header_timestamp,
        };
        let time_since_parent_ms = current_timestamp_secs.saturating_sub(parent_timestamp) * 1000;
        if time_since_parent_ms < self.config.miner.min_time_between_blocks_ms {
            debug!("Parent block mined {time_since_parent_ms} ms ago. Required minimum gap between blocks is {} ms", self.config.miner.min_time_between_blocks_ms;
                "current_timestamp" => current_timestamp_secs,
                "parent_block_id" => %stacks_parent_header.index_block_hash(),
                "parent_block_height" => stacks_parent_header.stacks_block_height,
                "parent_block_timestamp" => stacks_parent_header.burn_header_timestamp,
            );
            false
        } else {
            true
        }
    }
```
