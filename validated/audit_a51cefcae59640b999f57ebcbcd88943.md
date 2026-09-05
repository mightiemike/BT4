### Title
Attacker-forged "shadow block" bit causes signer-signature verification bypass and zero-signature block acceptance - (File: `stackslib/src/chainstate/nakamoto/mod.rs` / `stackslib/src/chainstate/nakamoto/shadow.rs`)

### Summary
`verify_signer_signatures`'s early return for `is_shadow_block()` grants full reward-set signing weight without inspecting `signer_signature` at all, and the guard meant to ensure only *legitimately inserted* shadow blocks take this path is defeated because `NakamotoStagingBlocksTx::store_block` unconditionally re-tags any incoming block's `obtain_method` as `Shadow` whenever the header's top version bit is set, regardless of how the block arrived. This lets an unprivileged block-submitter forge the shadow bit on a normal, non-emergency block and have it pass through the shadow validation branch, which never checks signer signatures.

### Finding Description
The claimed equality that must hold is: **accepted signer weight == sum of weights of signers whose valid signatures actually appear in `signer_signature`**. The shadow-block branch breaks this by substituting **accepted signer weight == full reward-set weight**, unconditionally, based solely on a single unauthenticated bit in the block header.

`NakamotoBlockHeader::is_shadow_block` is defined purely as a bit test on the wire-format `version` field: [1](#0-0) 

`get_shadow_signer_weight` sums the entire reward set's signer weight with no signature check whatsoever: [2](#0-1) 

The intended safety net is that shadow blocks can only enter chainstate via the emergency tooling path `add_shadow_block`, which enforces that the block is either the first block in a fresh tenure or part of an already-shadow tenure: [3](#0-2) 

and `validate_shadow_nakamoto_block_burnchain` additionally requires the block to already be recorded with `obtain_method = Shadow` before it will validate it via the (weaker) shadow-specific burnchain checks that explicitly skip VRF-proof and miner-signature verification: [4](#0-3) [5](#0-4) 

However, that "already stored as Shadow" precondition is trivially satisfiable by any attacker, because the *generic* staging-block insertion routine — the one used for ordinary network-received blocks (pushed/downloaded/uploaded) — silently overrides `obtain_method` to `Shadow` whenever the block's own header bit says so, with no check of provenance or of who inserted it: [6](#0-5) 

So the sequence for an attacker is:
1. Craft an ordinary Nakamoto block whose `signer_signature` is empty (or garbage), targeting a real tenure/consensus hash that the attacker controls or can align with (e.g., their own miner slot's tenure).
2. Set `block.header.version |= 0x80` exactly as the legitimate shadow-block builder does at `make_shadow_tenure`: [7](#0-6) 
3. Push/broadcast it through the normal block-relay path (not through `process_shadow_block`/`add_shadow_block`).
4. `store_block_if_better` → `store_block` stores it and auto-labels `obtain_method = Shadow` purely because of the header bit (line 681-686 above), independent of the fact it arrived over the network from an unprivileged peer.
5. Downstream validation dispatches to the shadow-specific path since `has_shadow_nakamoto_block_with_index_hash` now returns true (satisfied by the store-time override, not by any legitimate emergency insertion), skipping VRF/miner-signature checks.
6. `verify_signer_signatures` hits the `is_shadow_block()` branch and returns full reward-set weight, even though `signer_signature` is empty — bypassing the entire signer-weight/threshold consensus rule.

This breaks the SIGNING equality network-wide: every honest node runs the identical `store_block`/`verify_signer_signatures` logic, so they would *all* independently and deterministically accept this attacker block as if it carried full valid-signer weight, when in fact zero real signatures exist.

### Impact Explanation
This allows an invalid block (bypassing the entire threshold-signature consensus check that is supposed to gate every Nakamoto block) to be accepted network-wide by all honest nodes, since the vulnerable logic is deterministic and not something individual nodes can disagree on locally — it is a consensus-rule defect, not a local misconfiguration. Accepting a block with fabricated/absent signer approval undermines the entire signer-set security model (funds/state transitions in that block, including any STX transfers or tenure changes it carries, would be committed without any real signer consent), matching the "invalid block accepted... network-wide" Critical category.

### Likelihood Explanation
The attacker needs only the documented unprivileged capabilities: ability to submit a Nakamoto block over the network (already assumed baseline capability, e.g. a single miner slot or any p2p peer able to push a block). No signer key, no majority stake, and no privileged role is required — only the ability to set one bit in a block header before broadcasting. This is fully repeatable each time the attacker can get a shadow-tagged block accepted for processing.

### Recommendation
- Do not derive "is this a legitimate shadow block" purely from an unauthenticated header bit under attacker control. Gate the shadow-signature-bypass and the shadow-specific relaxed burnchain validation behind a state fact that cannot be forged over the network (e.g., a chainstate-schema/consensus-rule flag set only by the node's own upgrade/SIP application, not derived from data received from peers).
- In `NakamotoStagingBlocksTx::store_block`, do not let an externally-supplied block's own header bit unilaterally set `obtain_method = Shadow`; only the dedicated `add_shadow_block` call path (with its origin already known to be local/tooling-only) should ever produce `obtain_method = Shadow`.
- In `verify_signer_signatures`, never substitute assumed full weight for actual signature verification; if shadow blocks are meant to bypass signer consensus, that bypass must be enforced structurally (e.g., shadow blocks are never accepted via `Relayer::process_new_nakamoto_block`/network relay at all, only via the offline tooling path), not merely by trusting a version bit found in network-supplied bytes.

### Proof of Concept
Rust integration test plan (two-node/local-chainstate harness):
1. Stand up a Nakamoto testnet-style chainstate with a real signer/reward set of nonzero total weight `W`.
2. Construct a normal (non-emergency) `NakamotoBlock` for a real, upcoming tenure with `signer_signature = vec![]` (or a single obviously-invalid signature).
3. Set `block.header.version |= 0x80` (mirroring `shadow.rs` line 745) outside of `NakamotoBlockBuilder::make_shadow_tenure`/`process_shadow_block`.
4. Submit the block via the normal network path used by `Relayer::process_new_nakamoto_block` (not via `add_shadow_block`/`process_shadow_block`).
5. Assert equality #1 (expected, broken): `NakamotoChainState::verify_signer_signatures(&reward_set)` on this block returns `Ok(weight)` where `weight == W` (full weight) despite `signer_signature` containing zero valid entries.
6. Assert equality #2 (ground truth): manually recomputing weight from `signer_signature.iter().filter(|sig| verifies against a reward-set signer).sum()` yields `0`.
7. Assert that `#1 != #2`, proving the signature-verification bypass, and additionally assert that `Relayer::process_new_nakamoto_block`/`process_next_nakamoto_block` accepts and processes the block (i.e., `processed_block_receipt` is `Some`, and the block's `index_block_hash` becomes part of the canonical chain tip), demonstrating the invalid-block-accepted impact rather than mere internal function misbehavior.

### Citations

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L86-93)
```rust
    pub fn is_shadow_block(&self) -> bool {
        Self::is_shadow_block_version(self.version)
    }

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

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L176-202)
```rust
    ///
    /// NOTE: unlike normal blocks, we do not need to verify the VRF proof or miner signature
    pub(crate) fn validate_shadow_against_burnchain(
        &self,
        mainnet: bool,
        tenure_burn_chain_tip: &BlockSnapshot,
        expected_burn: Option<u64>,
    ) -> Result<(), ChainstateError> {
        if !self.is_shadow_block() {
            error!(
                "FATAL: tried to validate non-shadow block in a shadow-block-specific validator"
            );
            panic!();
        }
        self.common_validate_against_burnchain(tenure_burn_chain_tip, expected_burn)?;
        self.check_tenure_tx()?;
        self.check_shadow_coinbase_tx(mainnet)?;

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

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L261-311)
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

        let tenure_burn_chain_tip = Self::validate_nakamoto_tenure_snapshot(db_handle, block)?;
        if let Err(e) =
            block.validate_shadow_against_burnchain(mainnet, &tenure_burn_chain_tip, expected_burn)
        {
            warn!(
                "Invalid shadow Nakamoto block, could not validate on burnchain";
                "consensus_hash" => %block.header.consensus_hash,
                "stacks_block_hash" => %block.header.block_hash(),
                "block_id" => %block.header.block_id(),
                "error" => ?e
            );

            return Err(e);
        }
        Self::validate_nakamoto_block_static(
            mainnet,
            chain_id,
            db_handle.conn(),
            block,
            tenure_burn_chain_tip.block_height,
        )?;
        Ok(())
    }
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L745-751)
```rust
        shadow_block.header.version |= 0x80;

        // no need to sign with the signer set; just the miner is sufficient
        // (and it can be any miner)
        shadow_block.header.sign_miner(&miner_key)?;

        Ok(shadow_block)
```

**File:** stackslib/src/chainstate/nakamoto/shadow.rs (L820-848)
```rust
    pub fn add_shadow_block(&self, shadow_block: &NakamotoBlock) -> Result<(), ChainstateError> {
        if !shadow_block.is_shadow_block() {
            return Err(ChainstateError::InvalidStacksBlock(
                "Not a shadow block".into(),
            ));
        }
        let block_id = shadow_block.block_id();

        // is this block stored already?
        let qry = "SELECT 1 FROM nakamoto_staging_blocks WHERE index_block_hash = ?1";
        let args = params![block_id];
        let present: Option<i64> = query_row(self, qry, args)?;
        if present.is_some() {
            return Ok(());
        }

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

**File:** stackslib/src/chainstate/nakamoto/staging_blocks.rs (L665-692)
```rust
    /// Store a block into the staging DB.
    /// NOTE: This should not be made public
    fn store_block(
        &self,
        block: &NakamotoBlock,
        burn_attachable: bool,
        signing_weight: u32,
        obtain_method: NakamotoBlockObtainMethod,
    ) -> Result<(), ChainstateError> {
        let tenure_start = block.is_wellformed_tenure_start_block()?;
        let burn_attachable = burn_attachable || {
            // if it's burn_attachable before, it's burn_attachable always
            self.conn()
                .is_burn_block_processed(&block.header.consensus_hash)?
        };

        let obtain_method = if block.is_shadow_block() {
            // override
            NakamotoBlockObtainMethod::Shadow
        } else {
            obtain_method
        };

        if self.conn().is_shadow_tenure(&block.header.consensus_hash)? && !block.is_shadow_block() {
            return Err(ChainstateError::InvalidStacksBlock(
                "Tried to insert a non-shadow block into a shadow tenure".into(),
            ));
        }
```
