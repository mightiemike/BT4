### Title
Empty (non-`None`) signer set from legacy `make_signer_set` floor-and-drop division lets `verify_signer_signatures` accept an unsigned block via a `threshold == 0` degenerate case - (File: stackslib/src/chainstate/stacks/boot/mod.rs / stackslib/src/chainstate/nakamoto/mod.rs)

### Summary
`StacksChainState::make_signer_set` (the legacy PoX‑4/`RewardSetV0` path) can return `Some(vec![])` — a non‑empty input entries list where every individual signer's `stacked_amt / threshold` floors to `0` — because it uses a pure floor division with no "leftover" redistribution, unlike the newer `pox_5_make_signer_set`, which was specifically patched for this exact bug. `verify_signer_signatures` only rejects `None` from `reward_set.signers()`, so `Some(vec![])` passes through, making `total_signing_weight() == 0` and `compute_voting_weight_threshold(0) == 0`, so any signature set (including an empty one) trivially satisfies `total_weight_signed >= threshold`.

### Finding Description
The broken equality: "a block accepted as validly signed for cycle C" should require `total_weight_signed(signatures) >= threshold(real, non-degenerate stacker participation)`. Instead, when `reward_set.signers() == Some(vec![])`, the equality degenerates to `0 >= 0`, which is always true regardless of actual signatures.

Root cause is in `StacksChainState::make_signer_set`: [1](#0-0) 
This uses plain floor division (`stacked_amt / threshold`) and drops any entry whose result is `0`, with **no** largest-remainder/Hare round to guarantee at least one slot survives — exactly the "floor-and-drop" defect that was intentionally fixed in the newer PoX‑5 code path, as documented in the regression test comment: [2](#0-1) 
That fixed version (`pox_5_make_signer_set`) redistributes leftover slots so weight-0-for-everyone cannot happen, and additionally the PoX‑5 caller hard-fails on an empty result: [3](#0-2) [4](#0-3) 
No equivalent guard exists on the legacy `make_signer_set`/`RewardSetV0` path; it only returns `None` when the `entries` list itself is empty, not when every non‑zero entry rounds to weight 0: [5](#0-4) 

Downstream, `RewardSet::signers()` simply forwards the `Some(vec![])`: [6](#0-5) 
and `verify_signer_signatures` only guards against `None`, not an empty-but-`Some` vector: [7](#0-6) 
`total_signing_weight()` for an empty vec folds to `0`: [8](#0-7) 
and the check `total_weight_signed < threshold` becomes `0 < 0 == false`, so the function returns `Ok(0)` for an unsigned block: [9](#0-8) 

The same weak check (`Some(...) else { NoRegisteredSigners }`, no `is_empty()`) is also present in the signer-coordinator startup path, so honest signer nodes would independently compute the same degenerate `total_weight = 0`, `weight_threshold = 0`: [10](#0-9) 

The only place that explicitly checks `signers.is_empty()` is `get_signers_weights`, which is a different, unrelated code path (StackerDB coordinator initialization from a live Clarity read, not the consensus-critical `verify_signer_signatures`): [11](#0-10) 

Attacker's exact input: register (via normal `stack-stx`/`stack-extend` PoX-4 transactions) more distinct signer keys than `reward_slots` for a given cycle, each with roughly equal `stacked_amt`, such that being the dominant (or sole) participant in that cycle drives `threshold = ceil(total_ustx_locked / reward_slots)` above every individual entry's `stacked_amt`. This is the exact "equal stakes exceeding reward slots" scenario the regression test documents as previously causing the "entire signer set to be dropped." The resulting `RewardSetV0.signers = Some(vec![])` is deterministically computed and stored by all nodes from on-chain PoX-4 stacking state (no privileged role, node compromise, or majority of network STX supply required — only enough capital to dominate stacking participation in one PoX-4 reward cycle, split across more addresses than `reward_slots`).

### Impact Explanation
Because the reward set is derived deterministically from consensus-critical Clarity/chainstate data, all honest nodes (miners, followers, and signer nodes) independently compute the same `Some(vec![])` signer set and the same `threshold = 0`. This means an entirely unsigned Nakamoto block (`signer_signature = vec![]`) would be accepted network-wide as "fully signed" by `verify_signer_signatures`, which is the sole SIGNING gate for block acceptance. This matches the "Critical: an invalid block accepted... network-wide" category, since it is not a fork/divergence between nodes but a universal acceptance of a block that should be rejected for lack of real signer authorization, defeating the entire Nakamoto BFT signing security model for that reward cycle.

### Likelihood Explanation
Preconditions: a PoX‑4/`RewardSetV0` reward cycle (this bug is specific to the legacy `make_signer_set`, not the patched PoX‑5 `pox_5_make_signer_set`) where the attacker is the dominant/sole stacking participant and splits stake across more addresses than `reward_slots`, each holding roughly equal, sub-threshold amounts. This requires locking real STX (cost proportional to the size of that cycle's total stacked amount, not 51% of total network liquid supply) and does not require majority signer/miner status, node compromise, or any privileged role — only ordinary `stack-stx` transactions from many self-controlled addresses. Feasibility is highest in low-participation cycles (e.g., testnet, or early/quiet mainnet cycles) where the attacker can cheaply dominate total stacked volume; it is repeatable every such cycle until the underlying `make_signer_set` logic is patched.

### Recommendation
Patch `StacksChainState::make_signer_set` (stackslib/src/chainstate/stacks/boot/mod.rs:1020) to use the same largest-remainder/Hare-round distribution already implemented in `pox_5_make_signer_set`, guaranteeing at least one qualifying signer is never dropped when the input entry list is non-empty. Additionally, harden `verify_signer_signatures` and the StackerDB coordinator startup path to explicitly reject `Some(signers) if signers.is_empty()` (not just `None`), mirroring the existing `get_signers_weights` check, as defense in depth.

### Proof of Concept
Rust integration test plan (in `stackslib/src/chainstate/stacks/boot/signers_tests.rs` or `stackslib/src/chainstate/nakamoto/tests/mod.rs`):
1. Construct `RawRewardSetEntry` entries: N distinct signer keys (N > some small `reward_slots`-equivalent threshold value), each with `amount_stacked` slightly less than `threshold` computed as `ceil(total / N_slots)` such that `stacked_amt / threshold == 0` for every entry (mirror the setup in `equal_stakes_exceeding_reward_slots_are_not_all_zeroed`, but using `StacksChainState::make_signer_set` instead of `pox_5_make_signer_set`).
2. Call `StacksChainState::make_signer_set(threshold, &entries)` and assert it returns `Some(vec![])` (non-`None`, empty vec) — confirming the equality break at the source.
3. Build a `RewardSet::V0` with this `Some(vec![])` signers field (as done in the existing `make_reward_set` test helper in `stackslib/src/chainstate/nakamoto/tests/mod.rs:3668`).
4. Construct a `NakamotoBlockHeader::empty()` with `signer_signature = vec![]` (no signatures at all).
5. Call `header.verify_signer_signatures(&reward_set, StacksEpochId::latest())` and assert it returns `Ok(0)` instead of an error — confirming the unsigned block is accepted.
6. Contrast with a control assertion that `reward_set.total_signing_weight().unwrap() == 0` and `NakamotoBlockHeader::compute_voting_weight_threshold(0).unwrap() == 0`, demonstrating both sides of the "signed >= threshold" equality collapse to `0 >= 0`.

### Citations

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L493-503)
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
```

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L505-513)
```rust
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

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L763-766)
```rust
    /// intentionally a no-op.
    pub fn handle_pox_cycle_start_pox_5(
        _clarity: &mut ClarityTransactionConnection,
        _cycle_number: u64,
```

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L882-927)
```rust

    pub fn eval_boot_code_read_only(
        &mut self,
        sortdb: &SortitionDB,
        stacks_block_id: &StacksBlockId,
        boot_contract_name: &str,
        code: &str,
    ) -> Result<Value, Error> {
        let iconn = sortdb.index_handle_at_block(self, stacks_block_id)?;
        let ro_index = self.state_index.reopen_readonly()?;
        let headers_db = HeadersDBConn(StacksDBConn::new(&ro_index, ()));
        self.clarity_state
            .eval_read_only(
                stacks_block_id,
                &headers_db,
                &iconn,
                &boot::boot_code_id(boot_contract_name, self.mainnet),
                code,
            )
            .map_err(Error::ClarityError)
    }

    pub fn get_liquid_ustx(&mut self, stacks_block_id: &StacksBlockId) -> u128 {
        let mut connection = self.clarity_state.read_only_connection(
            stacks_block_id,
            &NULL_HEADER_DB,
            &NULL_BURN_STATE_DB,
        );
        connection
            .with_clarity_db_readonly_owned(|mut clarity_db| {
                (clarity_db.get_total_liquid_ustx(), clarity_db)
            })
            .expect("FATAL: failed to get total liquid ustx")
    }

    /// Determine the minimum amount of STX per reward address required to stack in the _next_
    /// reward cycle
    #[cfg(test)]
    pub fn get_stacking_minimum(
        &mut self,
        sortdb: &SortitionDB,
        stacks_block_id: &StacksBlockId,
    ) -> Result<u128, Error> {
        self.eval_boot_code_read_only(sortdb, stacks_block_id, "pox", "(get-stacking-minimum)")
            .map(|value| {
                value
```

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1020-1037)
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
```

**File:** stackslib/src/chainstate/stacks/boot/mod.rs (L1051-1065)
```rust
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
```

**File:** stackslib/src/chainstate/nakamoto/tests/signer_set.rs (L294-301)
```rust
#[test]
fn equal_stakes_exceeding_reward_slots_are_not_all_zeroed() {
    // Regression: more distinct signers than reward_slots, all with equal stake.
    //
    // The old floor-and-drop scheme set threshold = ceil(N*S / R) > S, so every
    // signer's weight floored to 0 and the entire set was dropped -- stalling the
    // chain. The Hare round must instead award one slot each to the top `R` signers
    // (by remainder, then signing_key), dropping only the surplus signers.
```

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

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L208-225)
```rust
        let total_weight = reward_set.total_signing_weight().map_err(|e| {
            warn!("Failed to calculate total weight for the reward set: {e:?}");
            ChainstateError::NoRegisteredSigners(0)
        })?;

        let weight_threshold = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)?;

        let reward_cycle_id = burnchain
            .block_height_to_reward_cycle(burn_tip.block_height)
            .expect("FATAL: tried to initialize coordinator before first burn block height");
        let signer_set =
            u32::try_from(reward_cycle_id % 2).expect("FATAL: reward cycle id % 2 exceeds u32");

        let Some(reward_set_signers) = reward_set.signers() else {
            error!("Could not initialize signing coordinator for reward set without signer");
            debug!("reward set: {reward_set:?}");
            return Err(ChainstateError::NoRegisteredSigners(0));
        };
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L1137-1144)
```rust
        if signers.is_empty() {
            error!(
                "No signers found for reward cycle";
                "reward_cycle" => reward_cycle,
            );
            return Err(ChainstateError::NoRegisteredSigners(reward_cycle));
        }
        Ok(signers)
```
