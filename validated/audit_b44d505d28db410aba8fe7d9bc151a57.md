[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L28-31)
```rust
/// Shadow blocks are blocks that are inserted directly into the staging blocks DB as part of a
/// schema update. They are neither mined nor relayed.  Instead, they are synthesized as part of an
/// emergency node upgrade in order to ensure that the conditions which lead to the chain stall
/// never occur.
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L90-93)
```rust
    /// Is a block version a shadow block version?
    pub fn is_shadow_block_version(version: u8) -> bool {
        version & 0x80 != 0
    }
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L95-107)
```rust
    /// Get the signing weight of a shadow block
    pub fn get_shadow_signer_weight(&self, reward_set: &RewardSet) -> Result<u32, Error> {
        let Some(signers) = reward_set.signers() else {
            return Err(ChainstateError::InvalidStacksBlock(
                "No signers in the reward set".to_string(),
            ));
        };
        let shadow_weight = signers
            .iter()
            .fold(0u32, |acc, signer| acc.saturating_add(signer.weight));

        Ok(shadow_weight)
    }
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L261-287)
```rust
    pub(crate) fn validate_shadow_nakamoto_block_burnchain(
        staging_db: NakamotoStagingBlocksConnRef,
        db_handle: &SortitionHandleConn,
        expected_burn: Option<u64>,
        block: &NakamotoBlock,
        mainnet: bool,
        chain_id: u32,
    ) -> Result<(), ChainstateError> {
        if !block.is_shadow_block() {
            error!(
                "FATAL: tried to validate non-shadow block in a shadow-block-specific validator"
            );
            panic!();
        }

        // this block must already be stored
        if !staging_db.has_shadow_nakamoto_block_with_index_hash(&block.block_id())? {
            warn!("Invalid shadow Nakamoto block, must already be stored";
                "consensus_hash" => %block.header.consensus_hash,
                "stacks_block_hash" => %block.header.block_hash(),
                "block_id" => %block.header.block_id()
            );

            return Err(ChainstateError::InvalidStacksBlock(
                "Shadow block must already be stored".into(),
            ));
        }
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L836-848)
```rust
        // this tenure must be empty, or it must be a shadow tenure
        let qry = "SELECT 1 FROM nakamoto_staging_blocks WHERE consensus_hash = ?1";
        let args = rusqlite::params![&shadow_block.header.consensus_hash];
        let present: Option<u32> = query_row(self, qry, args)?;
        if present.is_some()
            && !self
                .conn()
                .is_shadow_tenure(&shadow_block.header.consensus_hash)?
        {
            return Err(ChainstateError::InvalidStacksBlock(
                "Shadow block cannot be inserted into non-empty non-shadow tenure".into(),
            ));
        }
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L881-891)
```rust
/// DO NOT RUN ON A RUNNING NODE (unless you're testing).
///
/// Insert and process a shadow block into the Stacks chainstate.
pub fn process_shadow_block(
    chain_state: &mut StacksChainState,
    sort_db: &mut SortitionDB,
    shadow_block: NakamotoBlock,
) -> Result<(), ChainstateError> {
    let tx = chain_state.staging_db_tx_begin()?;
    tx.add_shadow_block(&shadow_block)?;
    tx.commit()?;
```
