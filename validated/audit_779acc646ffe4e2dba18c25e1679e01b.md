### Title
`NakamotoBlockHeader::verify_signer_signatures` accepts blocks with zero signer signatures when the reward set's signer list is `Some(empty_vec)` - (File: `stackslib/src/chainstate/nakamoto/mod.rs`)

### Summary
`verify_signer_signatures` only rejects a block when `reward_set.signers()` returns `None`; it does not reject the degenerate case where `signers()` returns `Some(&[])`. In that case `total_signing_weight()` returns `Ok(0)`, `compute_voting_weight_threshold(0)` returns `0`, and an empty `signer_signature` vector trivially satisfies `0 < 0 == false`, so the function returns `Ok(0)` for a completely unsigned block.

### Finding Description
The broken equality: a block accepted by `verify_signer_signatures` must have `total_weight_signed >= threshold` where `threshold` is derived from a legitimate, non-empty reward set representing real signer participation. Instead, when the reward cycle's signer set collapses to an empty (but `Some`) vector, both sides of the inequality degenerate to `0`, so the check is vacuously true.

Code path:
- `reward_set.signers()` returns `Option<&Vec<NakamotoSignerEntry>>`; only `None` triggers the "No signers in the reward set" error [1](#0-0) .
- `total_signing_weight()` folds over the signer list and returns `Ok(0)` for an empty (but present) list rather than an error — errors are only raised when `signers()` is `None` [2](#0-1) .
- `compute_voting_weight_threshold(0)` computes `(0 * 7) / 10 + 0 == 0` [3](#0-2) .
- With no signatures, the loop over `self.signer_signature` never executes, leaving `total_weight_signed = 0`, and `0 < 0` is false, so `Ok(0)` is returned [4](#0-3) .

Root cause / reachability: `make_signer_set` (used by the PoX-4 reward-set computation) filters out any entry whose computed `weight == 0` (`stacked_amt / threshold < 1`), but does not turn the whole set into `None` if every entry is filtered out — it returns `Some(vec![])` in that case, since the "no signer set" early return only fires when the raw `entries` list itself is empty [5](#0-4) . The PoX-4 path in `pox_4_compute_and_update_signers` explicitly anticipates a zero-participation outcome by passing `participation > 0` as a separate flag to `update_signers`, using `unwrap_or(&empty_signers)` as a fallback for the signer list [6](#0-5) , confirming that `signers() == Some(empty_vec)` is a reachable, known state for this code path. By contrast, `pox_5_compute_and_update_signers` explicitly guards against an empty signer set by returning `Err(ChainstateError::PoxNoRewardCycle)` [7](#0-6) , but no equivalent guard exists in the PoX-4 path or in `verify_signer_signatures` itself.

Exploit flow: an unprivileged miner waits for/produces a reward cycle where all stackers individually fall below the pox-4 per-slot minimum (so `make_signer_set` computes `weight == 0` for every entry and returns `Some(vec![])`), then submits a `NakamotoBlock` with `signer_signature: vec![]`. `verify_signer_signatures` returns `Ok(0)`, and the block is treated as validly signed by "the entire" (empty) reward set.

### Impact Explanation
Any node applying `verify_signer_signatures` during such a reward cycle will accept a block that carries zero signer signatures as fully valid, breaking the "signed by the cycle's reward set" consensus guarantee. This is a network-wide validation function used identically by every node, so an attacker (a single miner with a minority stake, no signer key required) can get an unsigned block accepted into the canonical chain — this is an invalid-block-accepted-network-wide condition, matching the Critical impact category.

### Likelihood Explanation
Requires a reward cycle where the PoX-4 signer-weight computation degenerates to an empty (non-`None`) signer list — i.e., every stacker in that cycle stacks less than the reward-slot threshold amount. This is a real, low-liquidity/low-participation network condition, not an attacker-forced Sybil/majority scenario; the attacker does not need to control stacking, only to observe or wait for such a cycle and then submit an unsigned block during it. No BTC cost beyond normal block/tenure production is required, and the exploit is repeatable every time such a cycle occurs.

### Recommendation
In `verify_signer_signatures`, treat an empty (but `Some`) signer list the same as `None` — return `ChainstateError::InvalidStacksBlock`/`NoRegisteredSigners` when `signers.is_empty()`, regardless of whether `signers()` returned `None` or `Some(&[])`. Additionally, `total_signing_weight()` should return an error (not `Ok(0)`) when the underlying signer list is empty, and the PoX-4 reward-set computation path should mirror the PoX-5 guard by failing reward-cycle computation (e.g., `PoxNoRewardCycle`) rather than producing a `RewardSet` with an empty `Some(vec![])` signer list.

### Proof of Concept
Rust integration test outline (extending the existing `stackslib/src/chainstate/nakamoto/tests/mod.rs` test harness, using `make_reward_set`/`pox_4_compute_and_update_signers` style helpers):
1. Build a `RewardSet::V0` with `signers: Some(vec![])` (simulating a cycle where every stacker's computed weight rounded to 0).
2. Construct a `NakamotoBlockHeader` with `signer_signature: vec![]`.
3. Assert `header.verify_signer_signatures(&reward_set, StacksEpochId::latest())` currently returns `Ok(0)` — this documents the bug.
4. After the fix, assert the same call returns `Err(ChainstateError::InvalidStacksBlock(_))` (or `NoRegisteredSigners`), i.e., that the equality `total_weight_signed(0) >= threshold(0)` is no longer treated as a valid consensus, and an empty reward-set explicitly rejects rather than trivially accepting zero-weight blocks.

### Citations

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1102-1107)
```rust
        let message = self.signer_signature_hash();
        let Some(signers) = reward_set.signers() else {
            return Err(ChainstateError::InvalidStacksBlock(
                "No signers in the reward set".to_string(),
            ));
        };
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1115-1189)
```rust
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

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1194-1207)
```rust
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

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L493-513)
```rust
    /// Return the total `weight` of all signers in the reward set.
    /// If there are no reward set signers, a ChainstateError is returned.
    pub fn total_signing_weight(&self) -> Result<u32, String> {
        let Some(signers) = self.signers() else {
            return Err("Unable to calculate total weight - No signers in reward set".to_string());
        };
        Ok(signers.iter().map(|s| s.weight).fold(0, |s, acc| {
            acc.checked_add(s)
                .expect("FATAL: Total signer weight > u32::MAX")
        }))
    }

    /// Return a reference to the signers list.
    /// V0 returns `None` if the signers field is `None`;
    /// Waterfall always has signers.
    pub fn signers(&self) -> Option<&Vec<NakamotoSignerEntry>> {
        match self {
            RewardSet::V0(v0) => v0.signers.as_ref(),
            RewardSet::Waterfall(wf) => Some(&wf.signers),
        }
    }
```

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1020-1071)
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
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L713-737)
```rust
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
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L763-766)
```rust
        if signer_set.is_empty() {
            error!("Fatal network condition: reward set computed with an empty signer set. Cannot continue producing blocks");
            return Err(ChainstateError::PoxNoRewardCycle);
        }
```
