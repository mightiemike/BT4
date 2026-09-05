## Finding

### Title
Nakamoto blocks can be accepted with zero signer signatures when PoX-4's "floor-and-drop" signer-set computation produces a non-empty-but-all-zero-weight signer set - ([File: stackslib/src/chainstate/nakamoto/mod.rs])

### Summary
`NakamotoBlockHeader::verify_signer_signatures` computes an acceptance threshold as a percentage of `reward_set.total_signing_weight()`. If the active reward cycle's signer set is `Some(vec![])` (present but empty) rather than `None`, `total_signing_weight()` returns `0`, so `compute_voting_weight_threshold(0)` also returns `0`. A block with **zero** signer signatures then satisfies `0 >= 0` and is accepted as valid. This degenerate "signers present but empty" state is reachable through the pre-existing PoX-4 "floor-and-drop" apportionment in `StacksChainState::make_signer_set`, which is not defended against by the reward-set-loading code the way the newer PoX-5 path defends against it.

### Finding Description
- `StacksChainState::make_signer_set` [1](#0-0)  aggregates PoX-4 stacker entries by signing key, then computes each signer's `weight = stacked_amt / threshold` and filters out any entry whose weight rounds down to `0`. If **every** aggregated signer's stake is individually below `threshold` (a "floor-and-drop" degenerate case that the project's own regression tests describe for PoX-4/PoX-5-style apportionment — "every signer's weight floored to 0 and the entire set was dropped"), the function returns `Some(vec![])`: the outer `Option` is `Some` (because raw entries existed), but the inner `Vec` is empty.
- `NakamotoSigners::pox_4_compute_and_update_signers` [2](#0-1)  takes this result and stores it directly as the cycle's `RewardSet`, with **no check** that the resulting signer set is non-empty. Contrast this with the newer `pox_5_compute_and_update_signers`, which explicitly guards: [3](#0-2) .
- When this reward set is later loaded by the coordinator, `read_reward_set_at_calculated_block` only rejects the reward set if `signers()` is `None`, not if it is `Some(empty)`: [4](#0-3) .
- Finally, `verify_signer_signatures` uses this reward set to check block signatures: [5](#0-4) . With `signers = Some(&vec![])`: the early `None`-check passes, `total_signing_weight()` folds an empty iterator to `0`, `signers_by_pk` is empty, the signature loop contributes nothing, `threshold = compute_voting_weight_threshold(0) = 0` [6](#0-5) , and `0 < 0` is `false`, so the function returns `Ok(0)` — success — even though `self.signer_signature` was empty.

This is the same bug class as the reported Solidity issue: a struct/field that is *unset in substance* (no real signer weight configured) is represented in a way indistinguishable from a *validly configured, zero-requirement* state, and the check that should gate on "is this actually configured" instead silently defaults to a value (`0`) that trivially satisfies the security equality (`weight_signed >= threshold`).

### Impact Explanation
This breaks the core Nakamoto consensus equality that a block must be endorsed by ≥70% of signer weight before it is valid. If a reward cycle's signer set degenerates to `Some(empty)`, **any** Nakamoto block header for that cycle — including one with an entirely empty `signer_signature` vector — passes `verify_signer_signatures`. Since all conforming nodes compute the same reward set deterministically from chain state, this would not by itself cause disagreement between honest nodes, but it removes the entire signer-quorum security guarantee for that cycle: a miner alone, without coordinating with or obtaining approval from any signer, could produce chain-valid blocks. This is a critical break of the signer-approval invariant (an "invalid block accepted network-wide" per the impact criteria), directly enabling unauthorized block production/tenure extension and associated reward capture without minority or majority stake control.

### Likelihood Explanation
No attacker action or privilege is required — it stems purely from ordinary PoX-4 stacking distribution. Any reward cycle where the aggregate stacked amount per distinct signing key never individually clears the reward-cycle `threshold` (e.g., because stacking participation is spread across many small stackers just under the per-slot threshold) will produce `make_signer_set() -> Some(vec![])`, and there is no downstream check preventing this degenerate, non-`None` empty signer list from being adopted as the cycle's active reward set.

### Recommendation
Change every "is the reward set/signer configuration real" check from `signers().is_none()` to `signers().map_or(true, |s| s.is_empty())`, mirroring the check already used in `OnChainRewardSetProvider::get_reward_set` [7](#0-6) . Specifically:
- `read_reward_set_at_calculated_block` in `stackslib/src/chainstate/nakamoto/coordinator/mod.rs` should reject `Some(empty)` signer sets, not just `None`.
- `pox_4_compute_and_update_signers` should mirror `pox_5_compute_and_update_signers`'s `signer_set.is_empty()` guard and hard-error (`PoxNoRewardCycle` or similar) instead of silently writing an empty signer set.
- As defense in depth, `verify_signer_signatures`/`total_signing_weight` should treat `total_weight == 0` as an error (`NoRegisteredSigners`) rather than a valid zero-threshold state.

### Proof of Concept
1. Construct (or naturally arrive at, via ordinary stacking behavior) a PoX-4 reward cycle where distinct signing keys' aggregated `stacked_amt` are each `< threshold`, so `make_signer_set` returns `Some(vec![])` — see the aggregation/filter logic at [8](#0-7) .
2. This propagates unchecked through `pox_4_compute_and_update_signers` into the stored `RewardSet` for the next cycle [9](#0-8) .
3. `read_reward_set_at_calculated_block` accepts it because `signers().is_none()` is `false` [10](#0-9) .
4. During that reward cycle, a miner submits a Nakamoto block header with `signer_signature = vec![]`. `verify_signer_signatures` computes `total_weight = 0`, `threshold = 0`, and `0 < 0` is false, so the block passes signature verification with zero signer approvals [11](#0-10) .

### Citations

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1020-1072)
```rust
    pub fn make_signer_set(
        threshold: u128,
        entries: &[RawRewardSetEntry],
    ) -> Option<Vec<NakamotoSignerEntry>> {
        let Some(first_entry) = entries.first() else {
            // entries is empty: there's no signer set
            return None;
        };
        // signing keys must be all-or-nothing in the reward set
        let expects_signing_keys = first_entry.signer.is_some();
        for entry in entries.iter() {
            if entry.signer.is_some() != expects_signing_keys {
                panic!("FATAL: stacking-set contains mismatched entries with and without signing keys.");
            }
        }
        if !expects_signing_keys {
            return None;
        }

        let mut signer_set = BTreeMap::new();
        for entry in entries.iter() {
            let signing_key = entry
                .signer
                .expect("BUG: signing keys should all be set in reward-sets with any signing keys");
            if let Some(existing_entry) = signer_set.get_mut(&signing_key) {
                *existing_entry += entry.amount_stacked;
            } else {
                signer_set.insert(signing_key, entry.amount_stacked);
            };
        }

        let mut signer_set: Vec<_> = signer_set
            .into_iter()
            .filter_map(|(signing_key, stacked_amt)| {
                let weight = u32::try_from(stacked_amt / threshold)
                    .expect("CORRUPTION: Stacker claimed > u32::max() reward slots");
                if weight == 0 {
                    return None;
                }
                Some(NakamotoSignerEntry {
                    signing_key,
                    stacked_amt,
                    weight,
                })
            })
            .collect();

        // finally, we must sort the signer set: the signer participation bit vector depends
        //  on a consensus-critical ordering of the signer set.
        signer_set.sort_by_key(|entry| entry.signing_key);

        Some(signer_set)
    }
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L703-738)
```rust
    fn pox_4_compute_and_update_signers(
        clarity: &mut ClarityTransactionConnection,
        pox_constants: &PoxConstants,
        reward_cycle: u64,
        pox_contract: &str,
        coinbase_height: u64,
    ) -> Result<SignerCalculation, ChainstateError> {
        let is_mainnet = clarity.is_mainnet();
        let signers_contract = &boot_code_id(SIGNERS_NAME, is_mainnet);

        let liquid_ustx = clarity.with_clarity_db_readonly(|db| db.get_total_liquid_ustx())?;
        let reward_slots = Self::get_pox_4_reward_slots(clarity, reward_cycle, pox_contract)?;
        let (threshold, participation) = StacksChainState::get_reward_threshold_and_participation(
            pox_constants,
            &reward_slots[..],
            liquid_ustx,
        );

        let reward_set =
            StacksChainState::make_reward_set(threshold, reward_slots, StacksEpochId::Epoch30);

        test_debug!("Reward set for cycle {}: {:?}", &reward_cycle, &reward_set);

        let empty_signers = vec![];
        let events = Self::update_signers(
            clarity,
            reward_cycle,
            reward_set.signers().unwrap_or(&empty_signers),
            signers_contract,
            participation > 0,
            coinbase_height,
            is_mainnet,
        )?;

        Ok(SignerCalculation { events, reward_set })
    }
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L763-766)
```rust
        if signer_set.is_empty() {
            error!("Fatal network condition: reward set computed with an empty signer set. Cannot continue producing blocks");
            return Err(ChainstateError::PoxNoRewardCycle);
        }
```

**File:** stackslib/src/chainstate/nakamoto/coordinator/mod.rs (L221-245)
```rust
        if reward_set
            .rewarded_addresses()
            .map_or(false, |addrs| addrs.is_empty())
        {
            // no one is stacking (V0 with empty rewarded_addresses)
            err_or_debug!(debug_log, "No PoX participation");
            return Err(Error::PoXAnchorBlockRequired);
        }

        inf_or_debug!(
            debug_log,
            "PoX reward set loaded from written block state";
            "reward_set_block_id" => %reward_set_block.index_block_hash(),
            "burn_block_hash" => %reward_set_block.burn_header_hash,
            "stacks_block_height" => reward_set_block.stacks_block_height,
            "burn_header_height" => reward_set_block.burn_header_height,
        );

        if reward_set.signers().is_none() {
            err_or_debug!(
                debug_log,
                "FATAL: PoX reward set did not specify signer set in Nakamoto"
            );
            return Err(Error::PoXAnchorBlockRequired);
        }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1097-1189)
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

        let total_weight = reward_set
            .total_signing_weight()
            .map_err(|_| ChainstateError::NoRegisteredSigners(0))?;

        // HashMap of <PublicKey, (Signer, Index)>
        let mut signers_by_pk: HashMap<_, _> = signers
            .iter()
            .enumerate()
            .map(|(i, signer)| (&signer.signing_key, (signer, i)))
            .collect();

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

        return Ok(total_weight_signed);
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1192-1207)
```rust
    /// Compute the threshold for the minimum number of signers (by weight) required
    /// to approve a Nakamoto block.
    pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
        let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
        let total_weight = u64::from(total_weight);
        let ceil = if (total_weight * threshold) % 10 == 0 {
            0
        } else {
            1
        };
        u32::try_from((total_weight * threshold) / 10 + ceil).map_err(|_| {
            ChainstateError::InvalidStacksBlock(
                "Overflow when computing nakamoto block approval threshold".to_string(),
            )
        })
    }
```

**File:** stackslib/src/chainstate/coordinator/mod.rs (L349-352)
```rust
        if is_nakamoto_reward_set && reward_set.signers().map_or(true, |s| s.is_empty()) {
            error!("FATAL: Signer sets are empty in a reward set that will be used in nakamoto"; "reward_set" => ?reward_set);
            return Err(Error::PoXAnchorBlockRequired);
        }
```
