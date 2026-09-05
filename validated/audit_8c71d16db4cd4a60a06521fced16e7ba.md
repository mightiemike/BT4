## No vulnerability found for this question.

**Rationale:**

The claim requires that `validate_parent_microblock_stream(..., verify_signatures=false)` be reachable with attacker-forged, unverified microblock headers whose signatures were "never checked," producing a poison payload that could later slip past `handle_poison_microblock`.

Tracing the only call site that passes `verify_signatures=false`, `extract_connecting_microblocks` at [1](#0-0) , the code comment explicitly states why: *"since we got the microblocks from staging, where their signatures were already validated, we don't need to validate them again."* The microblocks it operates on come from `find_parent_microblock_stream` → `load_microblock_stream_fork`, which reads from the `staging_microblocks` table. Microblocks only enter that table via `preprocess_streamed_microblock`, which unconditionally verifies each microblock's signature against the parent block's `microblock_pubkey_hash` before storage and rejects it otherwise: [2](#0-1) . So by the time `validate_parent_microblock_stream` is called with `verify_signatures=false`, every header has already passed cryptographic verification — there is no path for an attacker to inject garbage/mismatched signatures into this flow.

Even hypothetically, if a poison payload with bogus signatures reached the mempool/Clarity execution layer, `handle_poison_microblock` independently re-derives and cross-checks the signer via `check_microblock_header_signer`, which calls `check_recover_pubkey()` on each header and rejects if the recovered pubkey hashes differ: [3](#0-2) . It further requires that the recovered pubkey hash correspond to an actually-announced `microblock_pubkey_hash` at some height on the fork, or the transaction is rejected with "never seen in this fork": [4](#0-3) . An attacker without the real miner's private key cannot produce two headers that both recover to that real, previously-announced pubkey hash, since ECDSA recovery is not forgeable without the corresponding private key.

Thus the two guards the question asks about (staging-time signature verification, and `check_microblock_header_signer` inside `handle_poison_microblock`) both hold, and the equality "poison payload accepted == signer verified" is preserved end-to-end. There is no reachable attacker-controlled path producing a false slash from unverified/forged signatures.

### Citations

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L3451-3473)
```rust
        let pubkey_hash = if let Some(pubkh) = StacksChainState::load_block_pubkey_hash(
            &blocks_tx,
            &blocks_path,
            parent_consensus_hash,
            parent_anchored_block_hash,
        )? {
            pubkh
        } else {
            // don't have the parent
            return Ok(false);
        };

        let mut dup = microblock.clone();
        if let Err(e) = dup.verify(&pubkey_hash) {
            let msg = format!(
                "Invalid microblock {}: failed to verify signature with {}: {:?}",
                microblock.block_hash(),
                pubkey_hash,
                &e
            );
            warn!("{}", &msg);
            return Err(Error::InvalidStacksMicroblock(msg, microblock.block_hash()));
        }
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L5966-5992)
```rust
    /// Given the list of microblocks produced by the given block's parent (and given the parent's
    /// header info), determine which branch connects to the given block.  If there are multiple
    /// branches, punish the parent.  Return the portion of the branch that actually connects to
    /// the given block.
    pub fn extract_connecting_microblocks(
        parent_block_header_info: &StacksHeaderInfo,
        next_block_consensus_hash: &ConsensusHash,
        next_block_hash: &BlockHeaderHash,
        block: &StacksBlock,
        mut next_microblocks: Vec<StacksMicroblock>,
    ) -> Result<Vec<StacksMicroblock>, Error> {
        // NOTE: since we got the microblocks from staging, where their signatures were already
        // validated, we don't need to validate them again.
        let Some((microblock_terminus, _)) = StacksChainState::validate_parent_microblock_stream(
            parent_block_header_info
                .anchored_header
                .as_stacks_epoch2()
                .ok_or_else(|| Error::InvalidChildOfNakomotoBlock)?,
            &block.header,
            &next_microblocks,
            false,
        ) else {
            debug!(
                "Stopping at block {next_block_consensus_hash}/{next_block_hash} -- discontiguous header stream"
            );
            return Ok(vec![]);
        };
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L686-713)
```rust
    fn check_microblock_header_signer(
        mblock_hdr_1: &StacksMicroblockHeader,
        mblock_hdr_2: &StacksMicroblockHeader,
    ) -> Result<Hash160, Error> {
        let pkh1 = mblock_hdr_1.check_recover_pubkey().map_err(|e| {
            Error::InvalidStacksTransaction(
                format!("Failed to recover public key: {:?}", &e),
                false,
            )
        })?;

        let pkh2 = mblock_hdr_2.check_recover_pubkey().map_err(|e| {
            Error::InvalidStacksTransaction(
                format!("Failed to recover public key: {:?}", &e),
                false,
            )
        })?;

        if pkh1 != pkh2 {
            let msg = format!(
                "Invalid PoisonMicroblock transaction -- signature pubkey hash {} != {}",
                &pkh1, &pkh2
            );
            warn!("{}", &msg);
            return Err(Error::InvalidStacksTransaction(msg, false));
        }
        Ok(pkh1)
    }
```

**File:** stackslib/src/chainstate/stacks/db/transactions.rs (L750-782)
```rust
        // is this valid -- were both headers signed by the same key?
        let pubkh =
            StacksChainState::check_microblock_header_signer(mblock_header_1, mblock_header_2)?;

        let microblock_height_opt = env
            .global_context
            .database
            .get_microblock_pubkey_hash_height(&pubkh)?;
        let current_height = env.global_context.database.get_current_block_height();

        // for the microblock public key hash we had to process
        env.add_memory(20)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        // for the block height we had to load
        env.add_memory(4)
            .map_err(|e| Error::from_cost_error(e, cost_before.clone(), env.global_context))?;

        // was the referenced public key hash used anytime in the past
        // MINER_REWARD_MATURITY blocks?
        let mblock_pubk_height = match microblock_height_opt {
            None => {
                // public key has never been seen before
                let msg = format!(
                    "Invalid Stacks transaction: microblock public key hash {} never seen in this fork",
                    &pubkh
                );
                warn!("{}", &msg;
                      "microblock_pubkey_hash" => %pubkh
                );

                return Err(Error::InvalidStacksTransaction(msg, false));
            }
```
