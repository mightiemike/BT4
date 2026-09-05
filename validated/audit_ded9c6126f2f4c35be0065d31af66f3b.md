Based on my investigation of the signer weight/threshold verification (`stackslib/src/chainstate/nakamoto/mod.rs` `verify_signer_signatures`), the StackerDB listener's signature accounting (`stacks-node/src/nakamoto_node/stackerdb_listener.rs`), the sortition winner selection (`stackslib/src/chainstate/burn/sortition.rs`), and VRF/block-commit validation (`stackslib/src/chainstate/nakamoto/mod.rs`, `stackslib/src/chainstate/stacks/block.rs`), I found no code path where a permission/authorization check is skipped or bypassable by an unprivileged/minority actor in a way that breaks one of the required equalities (sortition winner vs. burn-weight function, signer weight from wrong set, non-reproducible state root, tenure not descending from sortition, double reward payment, or a validation-verdict split).

Specifically:
- Signer weight is always bound to slot_id → `signer_entries` (the canonical reward-cycle-derived signer set), and the ECDSA signature is verified to recover to that exact signer's pubkey before the weight is credited [1](#0-0) .
- The Nakamoto block-level threshold check (`verify_signer_signatures`) recovers each signature's pubkey, matches it against the reward-set-derived `signers_by_pk` map, enforces strict ordering, and only accepts if accumulated weight ≥ `compute_voting_weight_threshold` [2](#0-1) .
- Sortition winner selection is deterministically derived from the burn distribution, VRF seed, and sortition hash with no bypassable authorization gate <cite repo="Oyahkilomeikhide/stacks-core--012" path="stackslib/src/chainstate/burn/sortition.rs" start="120" ...

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L372-417)
```rust
            for (slot_id, _pk, message) in messages.into_iter() {
                let Some(signer_entry) = &self.signer_entries.get(&slot_id) else {
                    return Err(NakamotoNodeError::SignerSignatureError(
                        "Signer entry not found".into(),
                    ));
                };
                let Ok(signer_pubkey) = StacksPublicKey::from_slice(&signer_entry.signing_key)
                else {
                    return Err(NakamotoNodeError::SignerSignatureError(
                        "Failed to parse signer public key".into(),
                    ));
                };

                match message {
                    SignerMessageV0::BlockResponse(BlockResponse::Accepted(accepted)) => {
                        let BlockAccepted {
                            signer_signature_hash: block_sighash,
                            signature,
                            metadata,
                            response_data,
                        } = accepted;
                        let tenure_extend_timestamp = response_data.tenure_extend_timestamp;
                        let read_count_extend_timestamp =
                            response_data.tenure_extend_read_count_timestamp;

                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&block_sighash) else {
                            info!(
                                "StackerDBListener: Received signature for block that we did not request. Ignoring.";
                                "signature" => %signature,
                                "signer_signature_hash" => %block_sighash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

                        let Ok(valid_sig) = signer_pubkey.verify(block_sighash.bits(), &signature)
                        else {
                            warn!(
                                "StackerDBListener: Got invalid signature from a signer. Ignoring."
                            );
                            continue;
                        };
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1133-1187)
```rust
        for signature in self.signer_signature.iter() {
            let public_key = Secp256k1PublicKey::recover_to_pubkey_without_validating_low_s(
                message.bits(),
                signature,
            )
            .map_err(|_| {
                ChainstateError::InvalidStacksBlock(format!(
                    "Unable to recover public key from signature {}",
                    signature.to_hex()
                ))
            })?;

            let mut public_key_bytes = [0u8; 33];
            public_key_bytes.copy_from_slice(&public_key.to_bytes_compressed()[..]);

            let (signer, signer_index) = signers_by_pk.remove(&public_key_bytes).ok_or_else(|| {
                warn!(
                    "Found an invalid public key. Reward set has {} signers. Chain length {}. Signatures length {}",
                    signers.len(),
                    self.chain_length,
                    self.signer_signature.len(),
                );
                ChainstateError::InvalidStacksBlock(format!(
                    "Public key {} not found in the reward set",
                    public_key.to_hex()
                ))
            })?;

            // Enforce order of signatures
            if let Some(index) = last_index.as_ref() {
                if *index >= signer_index {
                    return Err(ChainstateError::InvalidStacksBlock(
                        "Signatures are out of order".to_string(),
                    ));
                }
                if strict_order {
                    last_index = Some(signer_index);
                }
            } else {
                last_index = Some(signer_index);
            }

            total_weight_signed = total_weight_signed
                .checked_add(signer.weight)
                .expect("FATAL: overflow while computing signer set threshold");
        }

        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }
```
