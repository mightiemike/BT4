Based on my investigation, this finding is invalid. Let me lay out why.

The premise depends on "two sortition winners racing at the same burn height" from a "duplicate-submission block-commit." That contradicts how Bitcoin-anchored sortition works in this codebase: a single burn block yields a single winning `LeaderBlockCommitOp` and thus a single `consensus_hash`/`sortition_id` per burn height on any given fork — sortition winner selection is a VRF-weighted single draw per snapshot, not a set. Two different `NakamotoBlock`s for "one tenure" cannot both be tenure-start blocks of the *same* sortition-winning tenure and both mature independently, because `check_nakamoto_tenure` requires the tenure-change payload's `tenure_consensus_hash` to equal `block_header.consensus_hash` and to correspond to a single valid, canonical sortition snapshot [1](#0-0) , and `check_tenure_tx` further ties the tenure-change to the actual miner pubkey and consensus hash of the block itself [2](#0-1) . A second competing block for that tenure would either be rejected as `DuplicateBlockFound` by the signer's tenure-change duplicate guard [3](#0-2) [4](#0-3) , or would land on an entirely different fork with its own `consensus_hash`, hence its own `index_block_hash`.

Reward maturation itself is fork-scoped, not global: `get_matured_miner_reward_schedules` resolves the payee row via `get_header_by_coinbase_height(tip_index_hash, matured_coinbase_height)`, which walks the ancestor chain of one specific `tip_index_hash` and returns exactly one `StacksHeaderInfo`/one `index_block_hash` [5](#0-4) [6](#0-5) . `get_scheduled_block_rewards_at_block` is then a plain keyed lookup by that single `index_block_hash` [7](#0-6) , and `insert_miner_payment_schedule` writes one row per `index_block_hash = StacksBlockId::new(consensus_hash, block_hash)` [8](#0-7) . Two competing blocks for the "same physical tenure" necessarily have different `consensus_hash`/`block_hash` pairs (different forks), so they produce two distinct, independently-maturing `index_block_hash` rows on two distinct forks — each fork's ledger state (including the STX balances that the poison commission is paid into) is disjoint. A node only ever executes one canonical fork at a time; there is no query or code path that sums or aggregates `MinerPaymentSchedule`/reward payouts *across* two different `index_block_hash` values belonging to different forks. Paying out on fork A's canonical history and paying out on fork B's canonical history are not "double-payment of the same commission" — they are two counterfactual, mutually exclusive chain states, exactly as intended by the fork-choice rule; whichever fork the network converges on is the one whose payment is ever realized in the actual, persistent chainstate.

The poison-microblock commission bookkeeping itself is also single-writer-scoped: `handle_poison_microblock` reads/writes the report keyed by `mblock_pubk_height` inside a single Clarity/MARF-backed chainstate transaction for the block being processed, and `insert_microblock_poison` only records one winning reporter (the earliest lower-sequence report) per fork's view [9](#0-8) ; `calculate_miner_reward`'s poison-commission slashing (`poison_microblock_commission`) is likewise computed once per `find_mature_miner_rewards` call, itself scoped to one `matured_miner_schedule.latest_miners` vector derived from one fork's `index_block_hash` [10](#0-9) .

No vulnerability found for this question.

### Citations

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L337-363)
```rust
    pub(crate) fn get_matured_miner_reward_schedules(
        chainstate_tx: &mut ChainstateTx,
        tip_index_hash: &StacksBlockId,
        coinbase_height: u64,
    ) -> Result<Option<MaturedMinerPaymentSchedules>, ChainstateError> {
        let mainnet = chainstate_tx.get_config().mainnet;

        // find matured miner rewards, so we can grant them within the Clarity DB tx.
        if coinbase_height < MINER_REWARD_MATURITY {
            return Ok(Some(MaturedMinerPaymentSchedules::genesis(mainnet)));
        }

        let matured_coinbase_height = coinbase_height - MINER_REWARD_MATURITY;
        let matured_tenure_block_header = Self::get_header_by_coinbase_height(
            chainstate_tx.deref_mut(),
            tip_index_hash,
            matured_coinbase_height,
        )?
        .ok_or_else(|| {
            warn!("Matured tenure data not found");
            ChainstateError::NoSuchBlockError
        })?;

        let latest_miners = StacksChainState::get_scheduled_block_rewards_at_block(
            chainstate_tx.deref_mut(),
            &matured_tenure_block_header.index_block_hash(),
        )?;
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L653-666)
```rust
    pub(crate) fn check_nakamoto_tenure<SH: SortitionHandle, SDBI: StacksDBIndexed>(
        headers_conn: &mut SDBI,
        sort_handle: &mut SH,
        block_header: &NakamotoBlockHeader,
        tenure_payload: &TenureChangePayload,
    ) -> Result<Option<NakamotoTenureEvent>, ChainstateError> {
        // block header must match this tenure
        if block_header.consensus_hash != tenure_payload.tenure_consensus_hash {
            warn!("Invalid tenure-change (or block) -- mismatched consensus hash";
                  "tenure_payload.tenure_consensus_hash" => %tenure_payload.tenure_consensus_hash,
                  "block_header.consensus_hash" => %block_header.consensus_hash
            );
            return Ok(None);
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1765-1802)
```rust
    pub(crate) fn check_tenure_tx(&self) -> Result<(), ChainstateError> {
        // If this block has a tenure-change, then verify that the miner public key is the same as
        // the leader key.  This is required for all tenure-change causes.
        let Some(tc_payload) = self.get_tenure_tx_payload() else {
            return Ok(());
        };

        // in all cases, the miner public key must match that of the tenure change
        let recovered_miner_hash160 = self.recover_miner_pubkh()?;
        if tc_payload.pubkey_hash != recovered_miner_hash160 {
            warn!(
                "Invalid tenure-change transaction -- bad miner pubkey hash160";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id(),
                "pubkey_hash" => %tc_payload.pubkey_hash,
                "recovered_miner_hash160" => %recovered_miner_hash160
            );
            return Err(ChainstateError::InvalidStacksBlock(
                "Invalid tenure change -- bad miner pubkey hash160".into(),
            ));
        }

        // in all cases, the tenure change's consensus hash must match the block's consensus
        // hash
        if tc_payload.tenure_consensus_hash != self.header.consensus_hash {
            warn!(
                "Invalid tenure-change transaction -- bad consensus hash";
                "consensus_hash" => %self.header.consensus_hash,
                "stacks_block_hash" => %self.header.block_hash(),
                "stacks_block_id" => %self.header.block_id(),
                "tc_payload.tenure_consensus_hash" => %tc_payload.tenure_consensus_hash
            );
            return Err(ChainstateError::InvalidStacksBlock(
                "Invalid tenure change -- bad consensus hash".into(),
            ));
        }

```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L2991-3024)
```rust
    pub fn get_header_by_coinbase_height<SDBI: StacksDBIndexed>(
        conn: &mut SDBI,
        tip_index_hash: &StacksBlockId,
        coinbase_height: u64,
    ) -> Result<Option<StacksHeaderInfo>, ChainstateError> {
        // nakamoto block?
        if let Some(block_id) =
            conn.get_nakamoto_block_id_at_coinbase_height(tip_index_hash, coinbase_height)?
        {
            return Self::get_block_header_nakamoto(conn.sqlite(), &block_id);
        }

        // epoch2 block?
        let Some(ancestor_at_height) = conn
            .get_ancestor_block_id(coinbase_height, tip_index_hash)?
            .map(|ancestor| Self::get_block_header(conn.sqlite(), &ancestor))
            .transpose()?
            .flatten()
        else {
            warn!("No such epoch2 ancestor";
                  "coinbase_height" => coinbase_height,
                  "tip_index_hash" => %tip_index_hash,
            );
            return Ok(None);
        };
        // only return if it is an epoch-2 block, because that's
        // the only case where block_height can be interpreted as
        // tenure height.
        if ancestor_at_height.is_epoch_2_block() {
            return Ok(Some(ancestor_at_height));
        }

        Ok(None)
    }
```

**File:** stacks-signer/src/chainstate/v1.rs (L505-518)
```rust
        let last_in_current_tenure = signer_db
            .get_last_globally_accepted_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stacks-signer/src/chainstate/v2.rs (L340-357)
```rust
        // We already confirmed in check miner activity that the current tenure is valid. So check we are not
        // reorging the tenure blocks. Only blocks we have signed (locally or globally accepted) count
        // here: a block we have merely pre-committed to carries no signature from us, so it is safe to
        // accept a competing tenure-start block in its place if it failed to reach consensus.
        let last_in_current_tenure = signer_db
            .get_last_signed_block(&block.header.consensus_hash)
            .map_err(|e| {
                SignerChainstateError::from(ClientError::InvalidResponse(e.to_string()))
            })?;
        if let Some(last_in_current_tenure) = last_in_current_tenure {
            warn!(
                "Miner block proposal contains a tenure change, but we've already signed a block in this tenure. Considering proposal invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "proposed_block_signer_signature_hash" => %block.header.signer_signature_hash(),
                "last_in_tenure_signer_signature_hash" => %last_in_current_tenure.block.header.signer_signature_hash(),
            );
            return Err(RejectReason::DuplicateBlockFound);
        }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L426-466)
```rust
    /// Schedule a miner payment in the future.
    /// Schedules payments out to both miners and users that support them.
    pub fn insert_miner_payment_schedule(
        tx: &mut DBTx,
        block_reward: &MinerPaymentSchedule,
    ) -> Result<(), Error> {
        assert!(block_reward.burnchain_commit_burn < i64::MAX as u64);
        assert!(block_reward.burnchain_sortition_burn < i64::MAX as u64);
        assert!(block_reward.stacks_block_height < i64::MAX as u64);

        let index_block_hash =
            StacksBlockId::new(&block_reward.consensus_hash, &block_reward.block_hash);

        let (payment_type, db_tx_fees_anchored, db_tx_fees_streamed) = match block_reward.tx_fees {
            MinerPaymentTxFees::Epoch2 { anchored, streamed } => {
                (HeaderTypeNames::Epoch2, anchored, streamed)
            }
            MinerPaymentTxFees::Nakamoto { parent_fees } => {
                (HeaderTypeNames::Nakamoto, parent_fees, 0)
            }
        };

        let args = params![
            block_reward.address.to_string(),
            block_reward.recipient.to_string(),
            block_reward.block_hash,
            block_reward.consensus_hash,
            block_reward.parent_block_hash,
            block_reward.parent_consensus_hash,
            block_reward.coinbase.to_string(),
            db_tx_fees_anchored.to_string(),
            db_tx_fees_streamed.to_string(),
            u64_to_sql(block_reward.burnchain_commit_burn)?,
            u64_to_sql(block_reward.burnchain_sortition_burn)?,
            u64_to_sql(block_reward.stacks_block_height)?,
            true,
            0i64,
            index_block_hash,
            payment_type,
            "0".to_string(),
        ];
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L706-718)
```rust
    /// Get the scheduled miner rewards at a particular index hash
    pub fn get_scheduled_block_rewards_at_block(
        conn: &DBConn,
        index_block_hash: &StacksBlockId,
    ) -> Result<Vec<MinerPaymentSchedule>, Error> {
        let qry =
            "SELECT * FROM payments WHERE index_block_hash = ?1 ORDER BY vtxindex ASC".to_string();
        let args = params![index_block_hash];
        let rows =
            query_rows::<MinerPaymentSchedule, _>(conn, &qry, args).map_err(Error::DBError)?;
        test_debug!("{} rewards in {}", rows.len(), index_block_hash);
        Ok(rows)
    }
```

**File:** stackslib/src/chainstate/stacks/db/accounts.rs (L982-1052)
```rust
    /// Find the latest miner reward to mature, assuming that there are mature rewards.
    /// Returns a list of payments to make to each address -- miners and user-support burners -- as
    /// well as an info struct about where the rewards took place on the chain.
    pub fn find_mature_miner_rewards(
        clarity_tx: &mut ClarityTx,
        sortdb_conn: &Connection,
        tip_stacks_height: u64,
        mut latest_matured_miners: Vec<MinerPaymentSchedule>,
        parent_miner: MinerPaymentSchedule,
    ) -> Result<Option<(MinerReward, Vec<MinerReward>, MinerReward, MinerRewardInfo)>, Error> {
        let mainnet = clarity_tx.config.mainnet;
        if tip_stacks_height <= MINER_REWARD_MATURITY {
            // no mature rewards exist
            return Ok(None);
        }

        let reward_height = tip_stacks_height - MINER_REWARD_MATURITY;

        let latest_matured_miners_head = latest_matured_miners
            .first()
            .expect("latest_matured_miners should not be empty");
        assert!(latest_matured_miners_head.vtxindex == 0);
        assert!(latest_matured_miners_head.miner);

        let users = latest_matured_miners.split_off(1);
        let miner = latest_matured_miners
            .pop()
            .expect("BUG: no matured miners despite prior check");

        let reward_info = MinerRewardInfo {
            from_stacks_block_hash: miner.block_hash.clone(),
            from_block_consensus_hash: miner.consensus_hash.clone(),
            from_parent_stacks_block_hash: parent_miner.block_hash.clone(),
            from_parent_block_consensus_hash: parent_miner.consensus_hash.clone(),
        };

        // what epoch was the parent miner's block evaluated in?
        let parent_evaluated_snapshot =
            SortitionDB::get_block_snapshot_consensus(sortdb_conn, &parent_miner.consensus_hash)?
                .expect("FATAL: no snapshot for evaluated block");

        let parent_evaluated_epoch =
            SortitionDB::get_stacks_epoch(sortdb_conn, parent_evaluated_snapshot.block_height)?
                .expect("FATAL: no epoch for evaluated block");

        // was this block penalized for mining a forked microblock stream?
        // If so, find the principal that detected the poison, and reward them instead.
        let poison_recipient_opt =
            StacksChainState::get_poison_microblock_report(clarity_tx, reward_height)?
                .map(|(reporter, _)| reporter);

        if let Some(ref _poison_reporter) = poison_recipient_opt.as_ref() {
            test_debug!(
                "Poison-microblock reporter {} at height {}",
                &_poison_reporter.to_string(),
                reward_height
            );
        } else {
            test_debug!("No poison-microblock report at height {}", reward_height);
        }

        // calculate miner reward
        let (parent_miner_reward, miner_reward) = StacksChainState::calculate_miner_reward(
            mainnet,
            parent_evaluated_epoch.epoch_id,
            &miner,
            &miner,
            &users,
            &parent_miner,
            poison_recipient_opt.as_ref(),
        );
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L805-856)
```rust
        // add punishment / commission record, if one does not already exist at lower sequence
        let (reporter_principal, reported_seq) = if let Some((reporter, seq)) = env
            .global_context
            .database
            .get_microblock_poison_report(mblock_pubk_height)?
        {
            // account for report loaded
            env.add_memory(u64::from(TypeSignature::PrincipalType.size().map_err(
                |_| Error::Expects("Failed to get size of PrincipalType".into()),
            )?))
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

            // u128 sequence
            env.add_memory(16)
                .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

            if mblock_header_1.sequence < seq {
                // this sender reports a point lower in the stream where a fork occurred, and is now
                // entitled to a commission of the punished miner's coinbase
                debug!("Sender {} reports a better poison-miroblock record (at {}) for key {} at height {} than {} (at {})", &sender_principal, mblock_header_1.sequence, &pubkh, mblock_pubk_height, &reporter, seq;
                    "sender" => %sender_principal,
                    "microblock_pubkey_hash" => %pubkh
                );
                env.global_context.database.insert_microblock_poison(
                    mblock_pubk_height,
                    &sender_principal,
                    mblock_header_1.sequence,
                )?;
                (sender_principal, mblock_header_1.sequence)
            } else {
                // someone else beat the sender to this report
                debug!("Sender {} reports an equal or worse poison-microblock record (at {}, but already have one for {}); dropping...", &sender_principal, mblock_header_1.sequence, seq;
                    "sender" => %sender_principal,
                    "microblock_pubkey_hash" => %pubkh
                );
                (reporter, seq)
            }
        } else {
            // first-ever report of a fork
            debug!(
                "Sender {} reports a poison-microblock record at seq {} for key {} at height {}",
                &sender_principal, mblock_header_1.sequence, &pubkh, &mblock_pubk_height;
                "sender" => %sender_principal,
                "microblock_pubkey_hash" => %pubkh
            );
            env.global_context.database.insert_microblock_poison(
                mblock_pubk_height,
                &sender_principal,
                mblock_header_1.sequence,
            )?;
            (sender_principal, mblock_header_1.sequence)
        };
```
