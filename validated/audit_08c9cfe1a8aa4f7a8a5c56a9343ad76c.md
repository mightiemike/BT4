## Analog Found

### Title
Miner-controlled Nakamoto block timestamp causes signer-verdict divergence during block-proposal validation - (File: `stackslib/src/net/api/postblock_proposal.rs`)

### Summary
`NakamotoChainState`/`NakamotoBlockProposal::validate` gates block-proposal acceptance on the miner-supplied `block.header.timestamp` being no more than 15 seconds ahead of each individual signer node's own local wall clock. Because that clock is not a network-synchronized or reproducible value, different signer nodes evaluating the identical block proposal at (nearly) the same real-world instant can independently reach opposite accept/reject verdicts, purely as a function of local clock skew that the block's single, unprivileged miner can freely position at the edge of.

### Finding Description
The `NakamotoBlockHeader.timestamp` field is chosen entirely by the block's miner — the only requirement enforced anywhere in the codebase is that it is strictly monotonic relative to the parent header and within 15 seconds of "now": [1](#0-0) 

This bound is checked in exactly one place — `NakamotoBlockProposal::validate`, which every signer node runs independently over its own `/v3/block_proposal` endpoint before deciding whether to sign: [2](#0-1) 

The future-bound check uses each signer's own `get_epoch_time_secs()` — a local, unsynchronized wall-clock read — rather than any value derived from the burnchain (e.g. the Bitcoin block's timestamp, which is itself the entity flagged in the original finding as miner-manipulable, or a consensus-agreed value). This is precisely the same bug class as the original report: a validity decision is being gated on a wall-clock timestamp that one party (here, the block-proposing miner) fully controls the placement of, and that different validating parties observe differently.

Notably, `NakamotoChainState::append_block`, which is the function that actually commits a block (once it has gathered signer approval) into the canonical chainstate that every full node reproduces, does not re-enforce this timestamp bound: [3](#0-2) 

so the "future timestamp" rule is a pure *proposal-acceptance* gate, evaluated independently and non-reproducibly by each signer against its own clock, rather than a deterministic, chain-state-level consensus rule.

### Impact Explanation
A single, unprivileged miner can craft a block header whose `timestamp` sits right at the edge of the `now + 15s` window. Because "now" is each signer's independent local clock (subject to ordinary NTP drift/skew, not an agreed value derived from chain data), some signers will accept the proposal (`Ok`) and others will reject it (`ValidateRejectCode::InvalidTimestamp`) for the very same block, at essentially the same wall-clock instant: [4](#0-3) 

This is a validation verdict that two nodes can legitimately disagree on for the same input, driven solely by a value the block's sole miner (a minority, unprivileged actor) supplies. The practical consequence is delayed/failed signature aggregation and temporary tip/proposal disagreement across the signer set for that tenure, until the miner retries with an adjusted timestamp — matching the "temporary tip disagreement" High-impact category. It does not, on its own, produce a permanent chain split or reward loss, since `append_block` does not re-derive or re-check this bound once a block is otherwise signed.

### Likelihood Explanation
Likelihood is real but bounded: any block-producing miner can trivially place `timestamp` near the boundary without any privilege beyond having won the current tenure's sortition (a normal, permission-less outcome), and ordinary clock skew across a decentralized signer set (which is explicitly not assumed to be tightly synchronized anywhere in this code) is sufficient to produce disagreement. It requires no majority or admin key — only the ordinary variance in signer nodes' local clocks, which the code neither bounds via a synchronized time source nor treats as a security assumption.

### Recommendation
Avoid conditioning proposal-validity decisions purely on each validator's independent local wall clock. Consider deriving the "future" bound from a value already agreed upon by the network (e.g. the burnchain tip's block timestamp with an appropriately wide tolerance, analogous to how burn-block time is already used elsewhere), or explicitly documenting/bounding the assumed maximum clock skew between signers so that the check's outcome does not depend on which specific signer evaluates it. At minimum, the `15` second window should be evaluated for sufficiency against realistic signer clock-skew distributions, and the divergent-verdict scenario should be treated as an expected transient condition with clear miner-retry/backoff behavior (which `stacks-node/src/nakamoto_node/miner.rs::validate_timestamp_info` partially does, but only against the miner's own clock, not the wider signer set's).

### Proof of Concept
1. A miner wins the current tenure's sortition (ordinary, unprivileged outcome).
2. The miner assembles a Nakamoto block with `header.timestamp = miner_local_time + 15` (the edge of the allowed window), satisfying `timestamp > parent.timestamp`.
3. The miner submits the block proposal to the signer set via `/v3/block_proposal`.
4. Each signer independently executes `NakamotoBlockProposal::validate`, comparing `self.block.header.timestamp` to its own `get_epoch_time_secs()`: [5](#0-4) 
5. Signers whose local clock is running slightly ahead of the miner's (a normal condition, not requiring any compromise) compute `block_timestamp > local_now + 15` and reject with `ValidateRejectCode::InvalidTimestamp`, while signers whose clock lags accept and sign — producing divergent verdicts on the identical block, as directly exercised by the existing test: [4](#0-3)

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L766-771)
```rust
    pub state_index_root: TrieHash,
    /// A Unix time timestamp of when this block was mined, according to the miner.
    /// For the signers to consider a block valid, this timestamp must be:
    ///  * Greater than the timestamp of its parent block
    ///  * At most 15 seconds into the future
    pub timestamp: u64,
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

**File:** stackslib/src/net/api/tests/postblock_proposal.rs (L490-514)
```rust
    let result = results.remove(0);
    match result {
        Ok(_) => panic!("expected error"),
        Err(postblock_proposal::BlockValidateReject {
            reason_code,
            reason,
            ..
        }) => {
            assert_eq!(reason_code, ValidateRejectCode::InvalidTimestamp);
            assert_eq!(reason, "Block timestamp is not greater than parent block");
        }
    }

    let result = results.remove(0);
    match result {
        Ok(_) => panic!("expected error"),
        Err(postblock_proposal::BlockValidateReject {
            reason_code,
            reason,
            ..
        }) => {
            assert_eq!(reason_code, ValidateRejectCode::InvalidTimestamp);
            assert_eq!(reason, "Block timestamp is too far into the future");
        }
    }
```
