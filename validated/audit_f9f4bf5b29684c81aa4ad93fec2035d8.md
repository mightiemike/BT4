### Title
Nakamoto block timestamp bound is enforced only in the signer's advisory RPC check, not in the canonical block-acceptance path - (File: `stackslib/src/chainstate/nakamoto/mod.rs`, `stackslib/src/net/api/postblock_proposal.rs`)

### Summary
The "greater than parent / at most 15s in the future" bound on `NakamotoBlockHeader.timestamp` is implemented only inside `BlockProposal::validate()` in `stackslib/src/net/api/postblock_proposal.rs#L637-669`, an advisory endpoint that signers call *before* they sign a block. The consensus-critical path that actually commits a Nakamoto block into chainstate — `NakamotoChainState::append_block` → `validate_normal_nakamoto_block_burnchain` → `validate_nakamoto_block_static` → `validate_header_static` / `validate_transactions_static` / `NakamotoBlock::validate_normal_against_burnchain` — never re-checks the timestamp bound.

### Finding Description
`NakamotoBlockHeader.timestamp` is documented as needing to be "greater than the timestamp of its parent block" and "at most 15 seconds into the future" [1](#0-0) , but this rule is only implemented in the signer-facing validation RPC:

```
if self.block.header.timestamp <= parent_nakamoto_header.timestamp { ... InvalidTimestamp ... }
...
if self.block.header.timestamp > get_epoch_time_secs() + 15 { ... InvalidTimestamp ... }
``` [2](#0-1) 

This is the only place in the codebase enforcing the bound. The functions that are actually invoked when a block is appended to the chain state — `validate_header_static` (checks only the header *version* field) [3](#0-2) , `validate_transactions_static` (checks tx uniqueness/network/chain-id/tenure well-formedness) [4](#0-3) , `validate_nakamoto_block_static` (wraps the two above) [5](#0-4) , and `validate_normal_nakamoto_block_burnchain` (checks tenure snapshot, block-commit, VRF/miner-key linkage) [6](#0-5)  — never look at `block.header.timestamp` at all. `NakamotoChainState::append_block`, the function that actually writes the block into `chainstate_tx`/MARF, calls exactly this validation chain and independently checks parent linkage, chain length, and tenure continuity, but not the timestamp [7](#0-6) .

Additionally, even the advisory check in `postblock_proposal.rs` only enforces monotonicity when the parent header is itself a Nakamoto header:
```
if let StacksBlockHeaderTypes::Nakamoto(parent_nakamoto_header) = &parent_stacks_header.anchored_header { ... }
``` [8](#0-7) 
For a block whose parent is an Epoch 2.x header (i.e., the very first Nakamoto block after the 3.0 transition), this monotonicity branch is skipped entirely, so only the "≤ now+15s" bound applies — the timestamp could still regress below the parent's `burn_header_timestamp`.

### Impact Explanation
Since the hard, consensus-enforced acceptance path (`append_block`/`validate_normal_nakamoto_block_burnchain`/`validate_nakamoto_block_static`) does not itself bound `timestamp`, the guarantee that Stacks block timestamps are monotonic and bounded is not a property of the chain-state transition function — it is only a convention that the current signer implementation happens to check before it signs. Any block that manages to obtain the required signer-threshold signature (e.g. due to divergence between the advisory check and the real rule, a bug/race in the signer's own validation, or future signer-software changes drifting from this repo's node-side rule) will be accepted unconditionally by every follower/relaying node that runs `append_block`, because that function performs no timestamp sanity check whatsoever. This mirrors the reported bug class exactly: the field that downstream logic (PoX reward-maturity windows, Clarity `get-block-info? time`, mempool/tx expiry) relies on for a monotonic notion of time is not actually validated by the code that commits state, only by an out-of-band advisory component.

### Likelihood Explanation
Exploitation does not require a majority attack on the network — it requires only that a single already-elected tenure miner (a legitimate minority actor for their own tenure) produce a block with an out-of-bound timestamp that nonetheless clears the (separately maintained, only-advisory) signer check, e.g. via the skipped-monotonicity branch at the Epoch 2.x→3.0 boundary, or any bug/version-skew in the signer's independent implementation of the same rule. Because the canonical `append_block` path performs zero verification of this field, there is no second line of defense once a signature threshold is obtained.

### Recommendation
Move the timestamp bound checks (`timestamp > parent.timestamp` and `timestamp <= now + max_future_secs`) out of the signer-only `postblock_proposal.rs::validate()` and into the canonical, always-executed validation chain (`NakamotoChainState::validate_normal_nakamoto_block_burnchain` / `validate_nakamoto_block_static` / `append_block`), so every node — not just signers casting an advisory vote — enforces the same hard rule on `NakamotoBlockHeader.timestamp` before committing a block to chainstate. Also remove the special-case skip of the monotonicity check when the parent header is a pre-Nakamoto header.

### Proof of Concept
1. A tenure's elected miner constructs a `NakamotoBlock` whose parent is the last Epoch 2.x block, and sets `header.timestamp` to a value less than the parent's `burn_header_timestamp` (allowed because the monotonicity branch in `postblock_proposal.rs#L640` is skipped for non-Nakamoto parents) — or, more generally, exploits any drift between the signer's advisory check and the actual rule to get signer signatures on an out-of-bound timestamp.
2. The block, now carrying a valid signer-signature threshold, is relayed to all nodes.
3. Every node calls `NakamotoChainState::append_block` → `validate_normal_nakamoto_block_burnchain` → `validate_nakamoto_block_static`, none of which inspect `header.timestamp` (`stackslib/src/chainstate/nakamoto/mod.rs#L1939-2033`, `#L2699-2817`, `#L5026-5113`), so the block is accepted and committed into the canonical chain state with the unvalidated timestamp.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L767-771)
```rust
    /// A Unix time timestamp of when this block was mined, according to the miner.
    /// For the signers to consider a block valid, this timestamp must be:
    ///  * Greater than the timestamp of its parent block
    ///  * At most 15 seconds into the future
    pub timestamp: u64,
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1939-1959)
```rust
    /// Static sanity checks on the block header that depend only on the epoch.
    /// Verifies:
    /// * the header version matches the epoch. The header version is fixed per
    ///   epoch and is what gates the `problematic_txs` field in the block hash,
    ///   so a block whose version doesn't match its epoch (ignoring the
    ///   shadow-block high bit) is rejected.
    pub fn validate_header_static(&self, epoch_id: StacksEpochId) -> bool {
        let expected_version = NakamotoBlockHeader::expected_version_for_epoch(epoch_id);
        if self.header.version & 0x7f != expected_version {
            warn!("Block has invalid header version for epoch";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id(),
                "epoch_id" => %epoch_id,
                "version" => self.header.version,
                "expected_version" => expected_version
            );
            return false;
        }
        true
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1961-2033)
```rust
    /// Static sanity checks on transactions.
    /// Verifies:
    /// * the block is non-empty
    /// * that all txs are unique
    /// * that all txs use the given network
    /// * that all txs use the given chain ID
    /// * if this is a tenure-start tx, that:
    ///    * it has a well-formed coinbase
    ///    * it has a sortition-induced tenure change transaction
    /// * that only epoch-permitted transactions are present
    pub fn validate_transactions_static(
        &self,
        mainnet: bool,
        chain_id: u32,
        epoch_id: StacksEpochId,
    ) -> bool {
        if self.txs.is_empty() {
            warn!("Block with zero transactions is invalid";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id()
            );
            return false;
        }
        if !StacksBlock::validate_transactions_unique(&self.txs)
            || !StacksBlock::validate_transactions_network(&self.txs, mainnet)
            || !StacksBlock::validate_transactions_chain_id(&self.txs, chain_id)
        {
            warn!("Block has duplicate transactions, invalid network, and/or invalid chain_id";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id()
            );
            return false;
        }
        if self.is_wellformed_tenure_start_block().is_err() {
            // bad tenure change
            warn!("Not a well-formed tenure-start block";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id()
            );
            return false;
        }
        if self.is_wellformed_tenure_extend_block().is_err() {
            // bad tenure extend
            warn!("Not a well-formed tenure-extend block";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id()
            );
            return false;
        };
        if !StacksBlock::validate_transactions_static_epoch(&self.txs, epoch_id) {
            warn!("Block has a transaction that is not supporteed in this epoch";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id(),
                "epoch_id" => %epoch_id
            );
            return false;
        }
        if let Err(e) = self.validate_problematic_txs(epoch_id) {
            warn!("Block has invalid problematic_txs markers: {e}";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id(),
                "epoch_id" => %epoch_id
            );
            return false;
        }
        true
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2699-2733)
```rust
    /// Statically validate the block's header and transactions against the
    /// burnchain epoch.
    /// Return Ok(()) if they pass all static checks
    /// Return Err(..) if not.
    fn validate_nakamoto_block_static(
        mainnet: bool,
        chain_id: u32,
        sortdb_conn: &Connection,
        block: &NakamotoBlock,
        block_tenure_burn_height: u64,
    ) -> Result<(), ChainstateError> {
        // Look up the epoch at this tenure's burn height. A Nakamoto block is
        // only ever valid in epoch 3.0 or later, so clamp the result up to
        // epoch 3.0. This is needed to handle the 2.5 -> 3.0 transition.
        let cur_epoch = SortitionDB::get_stacks_epoch(sortdb_conn, block_tenure_burn_height)?
            .expect("FATAL: no epoch defined for current Stacks block");
        let cur_epoch_id = cur_epoch.epoch_id.max(StacksEpochId::Epoch30);

        // static checks on the header and transactions all pass
        let valid = block.validate_header_static(cur_epoch_id)
            && block.validate_transactions_static(mainnet, chain_id, cur_epoch_id);
        if !valid {
            warn!(
                "Invalid Nakamoto block, failed static checks: {}/{} (epoch {})",
                &block.header.consensus_hash,
                &block.header.block_hash(),
                cur_epoch_id
            );
            return Err(ChainstateError::InvalidStacksBlock(
                "Invalid Nakamoto block: failed static checks".into(),
            ));
        }

        Ok(())
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2735-2817)
```rust
    /// Validate that a normal Nakamoto block attaches to the burn chain state.
    /// Called before inserting the block into the staging DB.
    /// Wraps `NakamotoBlock::validate_against_burnchain()`, and
    /// verifies that all transactions in the block are allowed in this epoch.
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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L5026-5113)
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
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L637-669)
```rust
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
