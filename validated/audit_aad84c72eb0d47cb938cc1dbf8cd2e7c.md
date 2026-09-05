[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L337-358)
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
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L524-532)
```rust
    pub fn get_ongoing_tenure<SDBI: StacksDBIndexed>(
        headers_conn: &mut SDBI,
        tip_block_id: &StacksBlockId,
    ) -> Result<Option<NakamotoTenureEvent>, ChainstateError> {
        let Some(tenure_id) = headers_conn.get_ongoing_tenure_id(tip_block_id)? else {
            return Ok(None);
        };
        Self::get_nakamoto_tenure_change(headers_conn.sqlite(), &tenure_id)
    }
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L847-863)
```rust
        let coinbase_height = match tenure_payload.cause {
            TenureChangeCause::BlockFound => {
                // tenure height advances
                parent_coinbase_height
                    .checked_add(1)
                    .expect("FATAL: too many tenures")
            }
            TenureChangeCause::Extended
            | TenureChangeCause::ExtendedRuntime
            | TenureChangeCause::ExtendedReadCount
            | TenureChangeCause::ExtendedReadLength
            | TenureChangeCause::ExtendedWriteCount
            | TenureChangeCause::ExtendedWriteLength => {
                // tenure height does not advance
                parent_coinbase_height
            }
        };
```

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L887-923)
```rust
    pub(crate) fn check_tenure_continuity<SDBI: StacksDBIndexed>(
        headers_conn: &mut SDBI,
        parent_ch: &ConsensusHash,
        block_header: &NakamotoBlockHeader,
    ) -> Result<bool, ChainstateError> {
        // block must have the same consensus hash as its parent
        if block_header.is_first_mined() || parent_ch != &block_header.consensus_hash {
            test_debug!("Block is discontinuous with tenure: either first-mined or has a different tenure ID";
                        "parent_ch" => %parent_ch,
                        "block_header.consensus_hash" => %block_header.consensus_hash,
                        "is_first_mined()" => block_header.is_first_mined(),
            );
            return Ok(false);
        }

        // block must be in the same tenure as the highest-processed tenure.
        let Some(highest_tenure) =
            Self::get_ongoing_tenure(headers_conn, &block_header.parent_block_id)?
        else {
            // no tenures yet, so definitely not continuous
            test_debug!("Block is discontinuous with tenure: no ongoing tenure";
                        "block_header.parent_block_id" => %block_header.parent_block_id,
            );
            return Ok(false);
        };

        if &highest_tenure.tenure_id_consensus_hash != parent_ch {
            // this block is not in the highest-known tenure, so it can't be continuous
            test_debug!("Block is discontinuous with tenure: parent is not in current tenure";
                        "parent_ch" => %parent_ch,
                        "highest_tenure.tenure_id_consensus_hash" => %highest_tenure.tenure_id_consensus_hash,
            );
            return Ok(false);
        }

        Ok(true)
    }
```
