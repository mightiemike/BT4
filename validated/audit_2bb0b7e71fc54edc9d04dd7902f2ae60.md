[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L962-990)
```rust
impl NakamotoBlockHeader {
    /// Whether a header at the given `version` includes the `problematic_txs`
    /// field in its serialization and signature/block-hash preimages.
    ///
    /// This is the single source of truth shared by `consensus_serialize`,
    /// `consensus_deserialize`, and both signature-hash calculations, so the
    /// field's presence can never diverge between them. The field was added in
    /// Epoch 4.0; version-0 headers omit it entirely.
    pub fn version_includes_problematic_txs(version: u8) -> bool {
        // The high bit (0x80) of `version` is the shadow-block flag; the header
        // version number is the low 7 bits. Mask it off before comparing so a
        // pre-4.0 shadow block (version 0x80) isn't mistaken for a v1 header.
        (version & 0x7f) >= NAKAMOTO_BLOCK_VERSION_EPOCH_4
    }

    /// The Nakamoto block header version required for blocks in `epoch_id`.
    ///
    /// The header format (and therefore the version number, ignoring the
    /// shadow-block high bit) is fixed per epoch: Epoch 4.0+ uses
    /// [`NAKAMOTO_BLOCK_VERSION_EPOCH_4`]; earlier Nakamoto epochs use
    /// [`NAKAMOTO_BLOCK_VERSION`]. Used to reject blocks whose version does not
    /// match their epoch.
    pub fn expected_version_for_epoch(epoch_id: StacksEpochId) -> u8 {
        if epoch_id >= StacksEpochId::Epoch40 {
            NAKAMOTO_BLOCK_VERSION_EPOCH_4
        } else {
            NAKAMOTO_BLOCK_VERSION
        }
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1001-1045)
```rust
    pub fn signer_signature_hash(&self) -> Sha512Trunc256Sum {
        self.signer_signature_hash_inner()
            .expect("BUG: failed to calculate signer signature hash")
    }

    /// Inner calculation of the message digest for miners to sign.
    /// This includes all fields _except_ the signatures.
    fn miner_signature_hash_inner(&self) -> Result<Sha512Trunc256Sum, CodecError> {
        let mut hasher = Sha512_256::new();
        let fd = &mut hasher;
        write_next(fd, &self.version)?;
        write_next(fd, &self.chain_length)?;
        write_next(fd, &self.burn_spent)?;
        write_next(fd, &self.consensus_hash)?;
        write_next(fd, &self.parent_block_id)?;
        write_next(fd, &self.tx_merkle_root)?;
        write_next(fd, &self.state_index_root)?;
        write_next(fd, &self.timestamp)?;
        write_next(fd, &self.pox_treatment)?;
        if Self::version_includes_problematic_txs(self.version) {
            write_next(fd, &self.problematic_txs)?;
        }
        Ok(Sha512Trunc256Sum::from_hasher(hasher))
    }

    /// Inner calculation of the message digest for stackers to sign.
    /// This includes all fields _except_ the stacker signature.
    fn signer_signature_hash_inner(&self) -> Result<Sha512Trunc256Sum, CodecError> {
        let mut hasher = Sha512_256::new();
        let fd = &mut hasher;
        write_next(fd, &self.version)?;
        write_next(fd, &self.chain_length)?;
        write_next(fd, &self.burn_spent)?;
        write_next(fd, &self.consensus_hash)?;
        write_next(fd, &self.parent_block_id)?;
        write_next(fd, &self.tx_merkle_root)?;
        write_next(fd, &self.state_index_root)?;
        write_next(fd, &self.timestamp)?;
        write_next(fd, &self.miner_signature)?;
        write_next(fd, &self.pox_treatment)?;
        if Self::version_includes_problematic_txs(self.version) {
            write_next(fd, &self.problematic_txs)?;
        }
        Ok(Sha512Trunc256Sum::from_hasher(hasher))
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1097-1120)
```rust
    pub fn verify_signer_signatures(
        &self,
        reward_set: &RewardSet,
        epoch_id: StacksEpochId,
    ) -> Result<u32, ChainstateError> {
        let message = self.signer_signature_hash();
        let Some(signers) = reward_set.signers() else {
            return Err(ChainstateError::InvalidStacksBlock(
                "No signers in the reward set".to_string(),
            ));
        };

        // if this is a shadow block, then its signing weight is as if every signer signed it, even
        // though the signature vector is undefined.
        if self.is_shadow_block() {
            return Ok(self.get_shadow_signer_weight(reward_set)?);
        }

        let mut total_weight_signed: u32 = 0;
        // `last_index` is used to prevent out-of-order signatures
        let mut last_index = None;
        // Before Epoch 4.0, signature order check contained a bug, so gate the
        // strict ordering behavior on the epoch.
        let strict_order = epoch_id.enforces_strict_signature_order();
```

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4053-4094)
```rust
    /// 1 < index 2 (the actual previous signer). From Epoch 4.0 onward the
    /// strict total-ordering rule rejects it.
    fn test_out_of_order_signer_signatures_after_first() {
        // Three signers, signed in index order [0, 2, 1]: the first pair (0, 2)
        // is in order, but the last pair (2, 1) is not.
        let signers = [
            (Secp256k1PrivateKey::random(), 100),
            (Secp256k1PrivateKey::random(), 100),
            (Secp256k1PrivateKey::random(), 100),
        ];
        let reward_set = make_reward_set(&signers);

        let mut header = NakamotoBlockHeader::empty();
        let message = header.signer_signature_hash().0;

        let signer_signature = [0, 2, 1]
            .iter()
            .map(|&i| {
                signers[i]
                    .0
                    .sign(&message)
                    .expect("Failed to sign block sighash")
            })
            .collect::<Vec<_>>();

        header.signer_signature = signer_signature;

        // Pre-4.0: the buggy partial-ordering rule accepts this sequence. The
        // weight (3 * 100, all signers) easily clears the threshold.
        header
            .verify_signer_signatures(&reward_set, StacksEpochId::Epoch30)
            .expect("Pre-4.0 must preserve the legacy (lenient) ordering behavior");

        // Epoch 4.0+: the strict total-ordering rule rejects it.
        match header.verify_signer_signatures(&reward_set, StacksEpochId::latest()) {
            Ok(_) => panic!("Expected out of order signatures to fail in Epoch 4.0"),
            Err(ChainstateError::InvalidStacksBlock(msg)) => {
                assert!(msg.contains("out of order"));
            }
            _ => panic!("Expected InvalidStacksBlock error"),
        }
    }
```
