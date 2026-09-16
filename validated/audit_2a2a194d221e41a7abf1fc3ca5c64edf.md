### Title
Proposer selection randomness is fixed and publicly known before staker weights are snapshotted, allowing stake-weight grinding to bias committee proposer selection - (File: `crates/apollo_staking/src/staking_manager.rs`)

### Summary
The committee/proposer selection logic in `apollo_staking` derives its pseudorandom value deterministically from a **already-committed, publicly known** block hash (the first block of the previous epoch), while the staker weights used to map that random value to a specific staker are fetched from the **current/latest** staking-contract state at the time the committee is (re)built, not snapshotted at the same block that produced the randomness. This mirrors the reported bug class: the "randomness" is fixed and observable before the "user input" (here, stake/delegation changes) is closed off, so a staker can react to already-known randomness by adjusting stake to steer the outcome.

### Finding Description
`proposer_randomness_block_hash` returns the hash of the **first block of the previous epoch** as the seed for the whole epoch's proposer selection: [1](#0-0) . This block hash becomes known and immutable as soon as that block is finalized — well before the new epoch (and its committee) begins.

`fetch_and_build_committee` then (a) fetches the *current* staker set via `self.staking_contract.get_stakers_with_config(epoch, &dynamic_config)`, (b) builds `cumulative_weights`/`total_weight` from that live snapshot, and only afterwards (c) calls `proposer_randomness_block_hash` to seed the generator: [2](#0-1) . There is no guarantee that the staker weights used to build `cumulative_weights` are pinned to the same block that produced the randomness seed — the committee is only actually materialized on a cache miss (`committee_at_height`), which can happen at any later point: [3](#0-2) .

The random value is then mapped deterministically to a proposer by scanning the cumulative weight ranges: [4](#0-3) , using a SHA-256-based generator whose only inputs are `height`, `round`, and the fixed block hash: [5](#0-4) .

Because the seed (`randomness_block_hash`, `height`, `round`) is fully known in advance and deterministic, any staker can compute in advance exactly which cumulative-weight range would make them (or a target) proposer for a future height/round. Since the weight snapshot used for `cumulative_weights` is taken from the live contract state rather than pinned to the same block that fixed the randomness, a staker can submit stake/delegation transactions *after* the randomness is already known to shift their own weight into (or out of) the winning range — analogous to accepting "input" (stake changes) after "randomness" has effectively already been fixed and is publicly computable.

### Impact Explanation
A staker able to predict and grind their stake weight against known-in-advance randomness can bias who becomes proposer for chosen heights/rounds. This is an unauthorized-account-action class impact on consensus: it lets a staker unfairly and predictably capture the proposer role (enabling censorship, MEV extraction, or griefing of the expected/deterministic proposer schedule) without requiring any operator/proposer malice — only ordinary staking-contract transactions from an unprivileged staker.

### Likelihood Explanation
Medium: it requires the attacker to be a staking-contract participant able to submit stake/delegation transactions, and requires timing them between the point the previous epoch's first block hash becomes known and the point the new epoch's committee is actually built/cached (`committee_at_height`/cache-miss path). Exact feasibility depends on the staking contract's own timing/snapshot rules for `get_stakers_with_config`, which were not fully inspectable from the indexed code — this introduces some uncertainty about whether the contract itself pins the staker snapshot to a specific block that would close this window.

### Recommendation
Pin the staker-weight snapshot used to build `cumulative_weights`/`total_weight` to the exact same (or an earlier, already-finalized) block that produces `randomness_block_hash`, so that no stake-affecting transaction submitted after the randomness seed becomes computable/known can influence the weights used for proposer selection. Equivalently, ensure the epoch's staker set is finalized/frozen strictly before the block whose hash seeds that epoch's randomness is produced.

### Proof of Concept
Conceptual (weight/timing manipulation, not directly executable from the indexed snippets alone):
1. Observe the finalized block hash of the first block of epoch `N-1` (this seeds all proposer randomness for epoch `N`), as computed in `proposer_randomness_block_hash`: [6](#0-5) .
2. Offline, compute `BlockPseudorandomGenerator::generate(height, round, total_weight)` for target `(height, round)` pairs using that hash, per [5](#0-4) , to determine which cumulative-weight range would select you as proposer.
3. Submit a stake increase/decrease (or delegation) transaction before the epoch-`N` committee is actually built (i.e., before the `committee_at_height` cache-miss path executes `fetch_and_build_committee`), shifting `cumulative_weights` so the pre-computed random value falls in your range: [7](#0-6) .
4. When the committee is finally fetched and cached, `get_proposer` will select you as proposer for the targeted height/round: [8](#0-7) .

### Citations

**File:** crates/apollo_staking/src/staking_manager.rs (L264-289)
```rust
    // Returns the committee data for the given epoch.
    // If the data is not cached, it is fetched from the state and cached.
    async fn committee_at_height(
        &self,
        height: BlockNumber,
    ) -> CommitteeProviderResult<Arc<Committee>> {
        let epoch = self.epoch_at_height(height).await?;

        // Attempt to read from cache.
        {
            let cache = self.committee_cache.lock().await;
            if let Some(committee) = cache.get(epoch) {
                return Ok(committee.clone());
            }
        }

        // Otherwise, build the committee from state, and cache the result.
        info!("Committee cache miss for epoch={epoch}, fetching from state.");
        let committee = Arc::new(self.fetch_and_build_committee(epoch).await?);

        // Cache the result.
        let mut cache = self.committee_cache.lock().await;
        cache.insert(epoch, committee.clone());

        Ok(committee)
    }
```

**File:** crates/apollo_staking/src/staking_manager.rs (L302-333)
```rust
        // Always use get_stakers_with_config - works for all implementations.
        let contract_stakers =
            self.staking_contract.get_stakers_with_config(epoch, &dynamic_config).await?;

        // Validate the staker set returned by the contract before building the committee.
        let contract_stakers = validate_stakers(contract_stakers)?;

        // Get the active committee config for this epoch (includes size and stakers).
        let active_config = get_config_for_epoch(
            &dynamic_config.default_committee,
            &dynamic_config.override_committee,
            epoch,
        );

        let committee_members =
            self.select_committee(contract_stakers, active_config.committee_size);

        // Prepare the data needed for proposer selection.
        let cumulative_weights: Vec<u128> = committee_members
            .iter()
            .scan(0, |acc, staker| {
                *acc = u128::checked_add(*acc, staker.weight.0).expect("Total weight overflow.");
                Some(*acc)
            })
            .collect();
        let total_weight = *cumulative_weights.last().unwrap_or(&0);

        let eligible_proposers =
            self.calculate_eligible_proposers(&committee_members, &active_config.stakers);

        // Calculate the randomness block hash for this epoch.
        let randomness_block_hash = self.proposer_randomness_block_hash(epoch).await?;
```

**File:** crates/apollo_staking/src/staking_manager.rs (L407-435)
```rust
    // Returns the block hash used for proposer selection randomness for the given epoch.
    // For epoch N, this returns the hash of the first block in epoch N-1.
    // Returns None for epoch 0 (first epoch has no previous epoch).
    // Assumes the epoch cache is synced at this point.
    async fn proposer_randomness_block_hash(
        &self,
        epoch: u64,
    ) -> CommitteeProviderResult<Option<BlockHash>> {
        // First epoch has no previous epoch.
        if epoch == 0 {
            return Ok(None);
        }

        let previous_epoch_id = epoch - 1;

        // Get the previous epoch from the cache.
        let prev_epoch = {
            let cache = self.epoch_cache.lock().await;
            cache.get_epoch(previous_epoch_id)
        };

        // If the cache is missing the previous epoch, treat it as an error.
        let prev_epoch = prev_epoch
            .ok_or(CommitteeProviderError::MissingInformation { epoch_id: previous_epoch_id })?;

        // Get the hash of the first block in the previous epoch.
        let block_hash = self.get_block_hash_with_fallback(prev_epoch.start_block).await?;
        Ok(Some(block_hash))
    }
```

**File:** crates/apollo_staking/src/staking_manager.rs (L510-540)
```rust
impl CommitteeTrait for Committee {
    fn get_proposer(&self, height: BlockNumber, round: Round) -> CommitteeResult<ContractAddress> {
        if self.use_only_actual_proposer_selection {
            return Ok(self.get_actual_proposer(height, round));
        }

        if self.committee_members.is_empty() {
            return Err(CommitteeError::EmptyCommittee);
        }

        // Check if we can return from cache.
        if let Some(address) = self
            .proposer_cache
            .lock()
            .expect("Mutex poisoned")
            .filter(|(h, r, _)| *h == height && *r == round)
            .map(|(_, _, addr)| addr)
        {
            return Ok(address);
        }

        // Generate a pseudorandom value in the range [0, total_weight) based on the height, round,
        // and the block hash stored in the generator.
        let random_value = self.random_generator.generate(height, round, self.total_weight);

        // Select a proposer from the committee using the generated random and update the cache.
        let proposer = self.choose_proposer(random_value);
        *self.proposer_cache.lock().expect("Mutex poisoned") = Some((height, round, proposer));

        Ok(proposer)
    }
```

**File:** crates/apollo_staking/src/utils.rs (L33-56)
```rust
impl BlockRandomGenerator for BlockPseudorandomGenerator {
    fn generate(&self, height: BlockNumber, round: Round, range: u128) -> u128 {
        if range == 0 {
            return 0;
        }
        let mut hasher = Sha256::new();

        hasher.update(height.0.to_be_bytes());
        hasher.update(round.to_be_bytes());
        if let Some(hash) = self.randomness_block_hash {
            hasher.update(hash.0.to_bytes_be().as_slice());
        } else {
            hasher.update([0u8; 32]);
        }

        let hash_bytes = hasher.finalize();

        // Since SHA256 is fixed 32 bytes, grab the last 16 bytes to extract a u128.
        let hash_value = u128::from_be_bytes(
            hash_bytes[16..32].try_into().expect("Failed to convert hash bytes to u128."),
        );
        // Return value in range [0, range).
        hash_value % range
    }
```
