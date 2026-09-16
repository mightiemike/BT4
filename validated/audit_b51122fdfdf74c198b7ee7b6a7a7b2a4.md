### Title
Fixed view-call resource budget in `call_view_entry_point` has no fallback path, letting staker growth permanently break committee/epoch queries needed to build blocks - (File: `crates/blockifier/src/execution/entry_point.rs`)

### Summary
The external report describes CToken's `borrowRatePerBlock`/`supplyRatePerBlock` reverting once enough blocks had passed because they were routed only through a gas-limited/`staticcall`-style read path (`delegateToViewImplementation`), with the only mitigation being a separate, non-view execution path (`delegateToImplementation`) that the affected functions never fell back to. The analogous root cause in this repo is `call_view_entry_point`, which bounds contract-view queries with hardcoded budgets (`VIEW_CALL_MAX_SIERRA_GAS`, `VIEW_CALL_MAX_N_STEPS`) sized "for a read rather than for a whole transaction," and is the *only* mechanism the batcher exposes for querying the staking contract. There is no equivalent to `delegateToImplementation` – no fallback path with a full transaction-sized resource budget.

### Finding Description
`call_view_entry_point` executes an external entry point against a `CachedState` whose writes are discarded, bounded by fixed constants: [1](#0-0) [2](#0-1) 

This is invoked exclusively by `Batcher::call_contract`, which is the only way `CairoStakingContract` reads staking-contract data (`get_stakers`, `get_current_epoch_data`, `get_previous_epoch_data`): [3](#0-2) [4](#0-3) 

Those queries feed `StakingManager::fetch_and_build_committee`, which builds the committee used for proposer selection and consensus: [5](#0-4) 

Because staking/delegation is a permissionless on-chain action (any unprivileged transaction sender can register as a staker), the staking contract's storage that `get_stakers` must iterate over (or an equivalent state-dependent view function) can grow arbitrarily via ordinary submitted transactions. Once the amount of work required to answer `get_stakers`/`get_current_epoch_data` exceeds the fixed `VIEW_CALL_MAX_SIERRA_GAS` / `VIEW_CALL_MAX_N_STEPS` budget, `call_view_entry_point` fails with a resource-exhaustion error (exactly analogous to the Compound `borrowRatePerBlock` reverting once `updateFrequency` blocks have passed). Unlike the Compound case, there is no alternate "full-transaction-budget" call path analogous to `delegateToImplementation` that the batcher/staking components can fall back to — `call_contract`/`call_view_entry_point` is the sole query mechanism, so the failure is permanent and unrecoverable without redeploying the contract, changing the constants, or migrating storage layout.

### Impact Explanation
`StakingContractError`/`CommitteeProviderError` bubble up from a failed `call_contract` and are surfaced to the consensus manager, which treats a failed committee/proposer lookup as fatal for that height/round (`VIRTUAL_PROPOSER_LOOKUP_FAILED`, dropping proposals, being unable to build proposals): [6](#0-5) [7](#0-6) 

If the underlying staking contract's read-only entry points require more computation than the fixed view-call budget once enough stakers have registered, every node's committee/epoch resolution fails simultaneously (since all nodes share the same fixed constants and same on-chain state), stalling proposer selection and block building network-wide — a "network unable to confirm new transactions" condition, which is the qualifying impact under the rules.

### Likelihood Explanation
Staking/staker-registration transactions are ordinary, permissionless, submitted transactions available to any unprivileged sender. Growing the staker set (or any other view-queried collection) to the point where the fixed step/gas ceiling is exceeded requires only enough such transactions, with no privileged access, and the trigger condition (state growth over time, analogous to "blocks mined since update" in the original report) is entirely predictable and reproducible by an attacker willing to submit enough transactions or wait for organic growth.

### Recommendation
- Give `call_view_entry_point` a resource budget that scales with (or is validated against) the actual state size / expected complexity of the entry points it is used for, or make the budget configurable and monitored so operators can react before it is exhausted.
- Provide a fallback execution path (analogous to `delegateToImplementation`) with a full block/transaction-sized resource budget for critical protocol queries such as `get_stakers`/`get_current_epoch_data`, so that hitting the view-call ceiling does not permanently break committee resolution.
- Add alerting when `call_contract` view calls repeatedly fail due to resource exhaustion (as distinct from ordinary business-logic reverts), and document the state-growth assumptions the fixed `VIEW_CALL_MAX_SIERRA_GAS`/`VIEW_CALL_MAX_N_STEPS` constants rely on so contract deployers can bound growth or paginate reads (e.g., `get_stakers` returning a bounded page rather than the full set).

### Proof of Concept
This cannot be fully demonstrated without the actual deployed staking contract's Cairo source (out of scope for this repository) and without knowledge of exactly how many stakers/how much storage is needed to exceed `VIEW_CALL_MAX_SIERRA_GAS` (100_000_000) / `VIEW_CALL_MAX_N_STEPS` (1_000_000). Conceptually:
1. Submit enough ordinary staking/delegation transactions (unprivileged, permissionless) to grow the staker set tracked by the staking contract.
2. Once `get_stakers` (or `get_current_epoch_data`, if implemented to iterate/aggregate state) requires more than the fixed view-call budget to execute, `Batcher::call_contract` → `call_view_entry_point` starts failing with a resource-exhaustion `EntryPointExecutionError`.
3. `CairoStakingContract::get_stakers`/`get_current_epoch` propagate this as `StakingContractError`, which `StakingManager::fetch_and_build_committee` surfaces as `CommitteeProviderError`.
4. Consensus's `get_proposer_for_height`/`initialize_single_height_consensus` fail for every node simultaneously (same contract state, same fixed constants), halting proposal building/validation network-wide.

This is confirmed at the code level (fixed-budget view call, no fallback path, and consensus treating committee-fetch failure as blocking), but the exact reproduction parameters (staker count/storage size needed) require access to the deployed staking contract's Cairo implementation, which is not present in this repository's index.

### Citations

**File:** crates/blockifier/src/execution/entry_point.rs (L50-61)
```rust
/// Sierra gas budget of a view entry point call. Bounds execution tracked by
/// [`TrackedResource::SierraGas`], the only bound on a natively executed contract, whose Cairo
/// steps are not counted at all. Equals `validate_max_sierra_gas`, and at every supported version's
/// `step_gas_cost` of 100 buys [`VIEW_CALL_MAX_N_STEPS`] steps, so both bounds are the same budget.
/// Pinned by `view_call_resource_bounds_match_versioned_constants`.
pub const VIEW_CALL_MAX_SIERRA_GAS: GasAmount = GasAmount(100_000_000);

/// Cairo step budget of a view entry point call. Bounds execution tracked by
/// [`TrackedResource::CairoSteps`], which consumes no Sierra gas: Cairo 0 classes, Cairo 1 classes
/// whose Sierra version predates `min_sierra_version_for_sierra_gas`, and every nested call made
/// once such a frame is on the stack. Equals `validate_max_n_steps`.
pub const VIEW_CALL_MAX_N_STEPS: usize = 1_000_000;
```

**File:** crates/blockifier/src/execution/entry_point.rs (L619-660)
```rust
// Calls the specified external entry point on the contract at the given address.
// Intended for view-only entry points; any attempted state changes will be discarded.
// Bounded by VIEW_CALL_MAX_SIERRA_GAS and VIEW_CALL_MAX_N_STEPS, since the caller is blocked while
// the call runs, so the budget is sized for a read rather than for a whole transaction.
pub fn call_view_entry_point(
    state_reader: impl StateReader,
    block_context: Arc<BlockContext>,
    storage_address: ContractAddress,
    entry_point_name: &str,
    calldata: Calldata,
) -> EntryPointExecutionResult<CallInfo> {
    let mut remaining_gas = VIEW_CALL_MAX_SIERRA_GAS;

    let execute_call = CallEntryPoint {
        entry_point_type: EntryPointType::External,
        entry_point_selector: selector_from_name(entry_point_name),
        calldata,
        class_hash: None,
        code_address: None,
        storage_address,
        caller_address: ContractAddress::default(),
        call_type: CallType::Call,
        initial_gas: remaining_gas.0,
    };

    // Create a dummy transaction info, since we are not in a context of a real transaction.
    let tx_context =
        Arc::new(TransactionContext { block_context, tx_info: TransactionInfo::default() });

    let limit_steps_by_resources = false;
    let mut context = EntryPointExecutionContext::new(
        tx_context,
        ExecutionMode::Execute,
        limit_steps_by_resources,
        SierraGasRevertTracker::new(remaining_gas),
    );
    // The context starts with the block's invoke_tx_max_n_steps, sized for a whole transaction.
    context.cap_remaining_steps(VIEW_CALL_MAX_N_STEPS);

    let mut state = CachedState::new(state_reader); // Changes to it are discarded.
    execute_call.non_reverting_execute(&mut state, &mut context, &mut remaining_gas.0)
}
```

**File:** crates/apollo_batcher/src/batcher.rs (L789-840)
```rust
    #[instrument(skip(self), err)]
    pub async fn call_contract(
        &self,
        input: CallContractInput,
    ) -> BatcherResult<CallContractOutput> {
        // Acquired before any storage access or state reader setup, so a call rejected under load
        // costs nothing beyond the semaphore check.
        let view_call_permit =
            self.view_call_semaphore.clone().try_acquire_owned().map_err(|_| {
                REJECTED_VIEW_CALLS.increment(1);
                warn!(
                    "Rejecting view call, all {MAX_CONCURRENT_VIEW_CALLS} view call slots are \
                     taken."
                );
                BatcherError::ContractCallFailed { reason: TOO_MANY_VIEW_CALLS_REASON.to_string() }
            })?;

        let height = self.get_height_from_storage()?;

        // Get the block info for the latest committed block.
        let block_info = match height.prev() {
            None => BlockInfo::default(),
            Some(last_committed_block) => self.get_block_info(last_committed_block)?,
        };

        let state_reader = self.view_state_reader_factory.create(
            height,
            self.config.dynamic_config.native_classes_whitelist.clone(),
            tokio::runtime::Handle::current(),
            self.config.dynamic_config.view_call_timeout_millis,
        );
        let block_context = BlockContext::new(
            block_info,
            self.config.static_config.block_builder_config.chain_info.clone(),
            self.versioned_constants(),
            BouncerConfig::max(),
        );

        let call_task = tokio::task::spawn_blocking(move || {
            // Owned by the blocking task, so the slot is freed only when the execution ends. A
            // caller that stops waiting (the view call timeout, a dropped connection) must not
            // free a slot that a still-parked thread occupies.
            let _view_call_permit = view_call_permit;
            call_view_entry_point(
                state_reader,
                Arc::new(block_context),
                input.contract_address,
                &input.entry_point,
                Calldata::from(input.calldata),
            )
            .map(|call_info| call_info.execution.retdata.0)
        });
```

**File:** crates/apollo_staking/src/cairo_staking_contract.rs (L36-93)
```rust
    async fn call_view(
        &self,
        entry_point: &str,
        calldata: Vec<Felt>,
    ) -> StakingContractResult<Retdata> {
        let output: CallContractOutput = self
            .batcher_client
            .call_contract(CallContractInput {
                contract_address: self.contract_address,
                entry_point: entry_point.to_string(),
                calldata,
            })
            .await?;
        Ok(Retdata(output.retdata))
    }
}

#[async_trait]
impl StakingContract for CairoStakingContract {
    async fn get_stakers(&self, epoch: u64) -> StakingContractResult<Vec<Staker>> {
        info!("Calling staking contract {GET_STAKERS_ENTRY_POINT} for epoch={epoch}.");
        let retdata = self.call_view(GET_STAKERS_ENTRY_POINT, vec![Felt::from(epoch)]).await?;

        // Filter out stakers that don't have a public key.
        let contract_stakers = ContractStaker::from_retdata_many(retdata)?;
        let initial_len = contract_stakers.len();
        let stakers: Vec<Staker> = contract_stakers
            .into_iter()
            .filter_map(|contract_staker| {
                contract_staker.public_key.map(|_| Staker::from(&contract_staker))
            })
            .collect();

        info!(
            "Retrieved {} stakers for epoch={}, filtered out {} without public key.",
            stakers.len(),
            epoch,
            initial_len - stakers.len()
        );

        Ok(stakers)
    }

    async fn get_current_epoch(&self) -> StakingContractResult<Epoch> {
        info!("Calling staking contract {GET_CURRENT_EPOCH_DATA_ENTRY_POINT}.");
        let retdata = self.call_view(GET_CURRENT_EPOCH_DATA_ENTRY_POINT, vec![]).await?;
        let epoch = Epoch::try_from(retdata)?;
        info!("Retrieved current epoch from contract: {epoch:?}.",);
        Ok(epoch)
    }

    async fn get_previous_epoch(&self) -> StakingContractResult<Option<Epoch>> {
        info!("Calling staking contract {GET_PREVIOUS_EPOCH_DATA_ENTRY_POINT}.");
        let retdata = self.call_view(GET_PREVIOUS_EPOCH_DATA_ENTRY_POINT, vec![]).await?;
        let epoch = CairoOption::<Epoch>::try_from(retdata)?.0;
        info!("Retrieved previous epoch from contract: {epoch:?}.");
        Ok(epoch)
    }
```

**File:** crates/apollo_staking/src/staking_manager.rs (L291-317)
```rust
    // Queries the state to fetch stakers for the given epoch and builds the full committee data.
    // This includes selecting the committee and preparing cumulative weights for proposer
    // selection, as well as calculating eligible proposers.
    async fn fetch_and_build_committee(&self, epoch: u64) -> CommitteeProviderResult<Committee> {
        // Update dynamic config to ensure we have the latest stakers config.
        self.update_dynamic_config().await;

        // Get the config to inject and use for committee building.
        // Clone it to avoid holding the lock across await.
        let dynamic_config = self.dynamic_config.read().expect("RwLock poisoned").clone();

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
```

**File:** crates/apollo_consensus/src/manager.rs (L1027-1037)
```rust
        match request {
            SMRequest::StartBuildProposal(round) => {
                let Ok(virtual_proposer) = committee.get_proposer(height, round) else {
                    warn!(
                        "VIRTUAL_PROPOSER_LOOKUP_FAILED: Failed to determine virtual proposer for \
                         height {height} round {round}. Proposal building will fail.",
                    );
                    let fut =
                        async move { StateMachineEvent::FinishedBuilding(None, round) }.boxed();
                    return Ok(Some(fut));
                };
```

**File:** crates/apollo_staking/src/committee_provider.rs (L34-58)
```rust
#[derive(Debug, Error)]
pub enum CommitteeProviderError {
    #[error("Committee is empty.")]
    EmptyCommittee,
    #[error("Committee info unavailable for height {height}.")]
    InvalidHeight { height: BlockNumber },
    #[error("Missing epoch information for epoch {epoch_id}.")]
    MissingInformation { epoch_id: u64 },
    #[error("Staking contract returned a duplicate staker address: {address}.")]
    DuplicateStakerAddress { address: ContractAddress },
    #[error("Staking contract returned no usable stakers (empty or all zero-weight).")]
    EmptyStakerSet,
    #[error(transparent)]
    StakingContractError(#[from] StakingContractError),
    #[error(
        "Failed retrieving block hash for block {block_number:?}, because both Batcher and State \
         Sync returned errors. Batcher error: {batcher_error:?}, State sync error: \
         {state_sync_error:?}"
    )]
    BlockHashFetchFailed {
        block_number: BlockNumber,
        batcher_error: BatcherClientError,
        state_sync_error: StateSyncClientError,
    },
}
```
