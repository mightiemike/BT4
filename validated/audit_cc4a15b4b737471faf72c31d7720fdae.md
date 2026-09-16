Confirmed: `L1EventsProviderConfig` (including `l1_handler_cancellation_timelock_seconds`) is a plain, static, per-node config field, not part of `NodeDynamicConfig` and not enforced identical across nodes by consensus or protocol constants.### Title
Locally-configured L1 handler cancellation timelock is not protocol-enforced, causing honest-node validation divergence on cancelled L1→L2 messages - (File: crates/apollo_l1_events/src/transaction_record.rs)

### Summary
The report describes a bug where a mutable configuration parameter (`_gracePeriod`) that affects the validity window of a pending action is read live rather than snapshotted per-action, letting a config change retroactively alter whether a specific pending item should still be considered valid/expired. The sequencer has a structurally analogous pattern: `l1_handler_cancellation_timelock_seconds`, which determines whether a requested L1-handler cancellation has "expired" and should flip a transaction from `Validated` to `Invalid(CancelledOnL2)`, is a plain per-node config value rather than a protocol-agreed constant, and it is read fresh at validation time against each node's own clock/config.

### Finding Description
`L1EventsProviderConfig::l1_handler_cancellation_timelock_seconds` is declared as an ordinary (non-dynamic, non-versioned) config field [1](#0-0) , loaded once per node from local config/deployment JSON files such as `l1_events_provider_config.json`/`replacer_l1_events_provider_config.json` [2](#0-1) , and wired into `NodeConfig` as a plain `Option<L1EventsProviderConfig>` field alongside other non-dynamic configs — distinct from fields explicitly tracked as `*DynamicConfig` (`ConsensusDynamicConfig`, `ContextDynamicConfig`, `BatcherDynamicConfig`, `GatewayDynamicConfig`, etc.) that are the ones kept synchronized/validated for cross-node consistency [3](#0-2) .

This timelock value is used directly to decide the runtime state of a pending L1 handler transaction whose cancellation was requested on L1: `TransactionRecord::update_time_based_state` compares `unix_now` against `cancellation_requested_at + cancellation_timelock` (taken from the node's local `TransactionRecordPolicy`) to decide whether the transaction transitions to `CancelledOnL2` [4](#0-3) . `TransactionManager::validate_tx` calls this on every `validate()` invocation using `self.config.l1_handler_cancellation_timelock_seconds`, and returns `Invalid(CancelledOnL2)` once the local timelock is judged to have passed, or `Validated` otherwise [5](#0-4) . Because there is no per-transaction snapshot of the timelock duration and no protocol-level (versioned-constants) enforcement of this value, two honest nodes running with different `l1_handler_cancellation_timelock_seconds` settings will disagree, at the exact same wall-clock time and consensus height/round, on whether a given L1-handler transaction that has an outstanding cancellation request is still validatable. This is the direct analog of the reported bug class: a configurable "grace period"/timelock is applied dynamically at check-time to a specific action rather than being fixed as an immutable parameter of that action, so changing/differing the parameter changes the outcome for actions that were already pending when the parameter was set.

### Impact Explanation
During block validation, `validate(tx_hash, height)` is called by the batcher against each proposed L1-handler transaction as part of `apollo_l1_events` validation flow (used within block-building/validation, cf. `crates/apollo_l1_events/tests/timing_flows.rs`) [6](#0-5) . If a proposer includes an L1-handler transaction whose cancellation request is within one node's stricter/looser timelock window but not another's, validators with a differently configured `l1_handler_cancellation_timelock_seconds` will return conflicting `ValidationStatus` for the same transaction at the same real time, causing some honest validators to accept the proposal and others to reject it. This is a consensus-safety-relevant divergence between honest nodes (not attacker-controlled) that can stall block finalization or, in the worst case, contribute to chain forks/failed rounds — matching the "honest-node divergence" / "network unable to confirm new transactions" impact categories.

### Likelihood Explanation
This does not require any malicious operator action to be exploitable — it only requires that different node operators run the (default-shipped) `L1EventsProviderConfig` with different values for `l1_handler_cancellation_timelock_seconds` (a legitimate, publicly documented config knob, not flagged anywhere as a value that must be network-wide identical) [7](#0-6) . Any L1 sender who requests a cancellation of a pending L1→L2 message near the boundary of the timelock window will naturally trigger the described divergence between validators with differing configured timelocks, making the divergence readily reachable via a normal L1 cancellation request combined with realistic sequencer deployment heterogeneity.

### Recommendation
Treat the L1-handler cancellation grace period as a network-wide protocol parameter (e.g., part of `VersionedConstants`/consensus-agreed dynamic config) rather than a purely local node config, and/or snapshot the effective timelock deadline at the moment the cancellation request is first observed/recorded in `TransactionRecord` so later config changes or per-node configuration differences cannot retroactively change the validity outcome for an already-pending cancellation.

### Proof of Concept
1. Deploy two honest sequencer nodes, Node A with `l1_handler_cancellation_timelock_seconds = 300` and Node B with `l1_handler_cancellation_timelock_seconds = 600` (both legitimate values from the shipped config templates [8](#0-7) ).
2. An L1 message sender submits a `LogMessageToL2` and, shortly after, a `MessageToL2CancellationStarted` request for that message.
3. A proposer includes the L1-handler transaction in a proposal at a time `t` such that `300 < (t - cancellation_requested_at) < 600` seconds.
4. Node A's `TransactionManager::validate_tx` (via `update_time_based_state`) marks the tx `CancelledOnL2` and rejects the proposal (`Invalid`) [9](#0-8) , while Node B still returns `Validated` for the same tx at the same time [10](#0-9) , demonstrating a concrete honest-node validation divergence purely from a locally configurable timelock parameter.

### Citations

**File:** crates/apollo_l1_events_config/src/config.rs (L12-24)
```rust
#[derive(Clone, Copy, Debug, Serialize, Deserialize, Validate, PartialEq, Eq)]
pub struct L1EventsProviderConfig {
    #[serde(deserialize_with = "deserialize_float_seconds_to_duration")]
    pub startup_sync_sleep_retry_interval_seconds: Duration,
    #[serde(deserialize_with = "deserialize_float_seconds_to_duration")]
    pub l1_handler_cancellation_timelock_seconds: Duration,
    #[serde(deserialize_with = "deserialize_float_seconds_to_duration")]
    pub l1_handler_consumption_timelock_seconds: Duration,
    #[serde(deserialize_with = "deserialize_float_seconds_to_duration")]
    pub l1_handler_proposal_cooldown_seconds: Duration,
    /// When true, the L1 provider operates in dummy mode.
    pub dummy_mode: bool,
}
```

**File:** crates/apollo_deployments/resources/app_configs/replacer_l1_events_provider_config.json (L1-7)
```json
{
  "l1_events_provider_config.dummy_mode": false,
  "l1_events_provider_config.l1_handler_cancellation_timelock_seconds": 300,
  "l1_events_provider_config.l1_handler_consumption_timelock_seconds": 300.0,
  "l1_events_provider_config.l1_handler_proposal_cooldown_seconds": 70,
  "l1_events_provider_config.startup_sync_sleep_retry_interval_seconds": 2
}
```

**File:** crates/apollo_node_config/src/node_config.rs (L234-281)
```rust
#[derive(Debug, Deserialize, Serialize, Clone, PartialEq, Validate)]
pub struct SequencerNodeConfig {
    /// If true, the node validates proposed blocks but does not build proposals.
    /// Requires gateway, http_server, and mempool to be disabled.
    pub validation_only: bool,
    // Infra related configs.
    #[validate(nested)]
    pub components: ComponentConfig,
    #[validate(nested)]
    pub config_manager_config: Option<ConfigManagerConfig>,
    #[validate(nested)]
    pub monitoring_config: MonitoringConfig,
    // Business-logic component configs.
    #[validate(nested)]
    pub base_layer_config: Option<EthereumBaseLayerConfig>,
    #[validate(nested)]
    pub batcher_config: Option<BatcherConfig>,
    #[validate(nested)]
    pub class_manager_config: Option<FsClassManagerConfig>,
    #[validate(nested)]
    pub committer_config: Option<ApolloCommitterConfig>,
    #[validate(nested)]
    pub consensus_manager_config: Option<ConsensusManagerConfig>,
    #[validate(nested)]
    pub gateway_config: Option<GatewayConfig>,
    #[validate(nested)]
    pub http_server_config: Option<HttpServerConfig>,
    #[validate(nested)]
    pub l1_gas_price_provider_config: Option<L1GasPriceProviderConfig>,
    #[validate(nested)]
    pub l1_gas_price_scraper_config: Option<L1GasPriceScraperConfig>,
    #[validate(nested)]
    pub l1_events_provider_config: Option<L1EventsProviderConfig>,
    #[validate(nested)]
    pub l1_events_scraper_config: Option<L1EventsScraperConfig>,
    #[validate(nested)]
    pub mempool_config: Option<MempoolConfig>,
    #[validate(nested)]
    pub mempool_p2p_config: Option<MempoolP2pConfig>,
    #[validate(nested)]
    pub monitoring_endpoint_config: Option<MonitoringEndpointConfig>,
    #[validate(nested)]
    pub proof_manager_config: Option<ProofManagerConfig>,
    #[validate(nested)]
    pub sierra_compiler_config: Option<SierraCompilationConfig>,
    #[validate(nested)]
    pub state_sync_config: Option<StateSyncConfig>,
}
```

**File:** crates/apollo_l1_events/src/transaction_record.rs (L191-209)
```rust
    /// Update the state of the record based on the current time and policy.
    /// This updates the state based on time-based state transitions, such as moving from
    /// CancellationStartedOnL2 to CancelledOnL2 after the timelock expires.
    pub fn update_time_based_state(&mut self, unix_now: u64, policy: TransactionRecordPolicy) {
        if let Some(requested_at) = self.cancellation_requested_at {
            if self.committed {
                return; // Committing overrides cancellations.
            }

            let cancellation_timelock = &policy.cancellation_timelock.as_secs();
            let is_cancellation_timelock_passed =
                unix_now >= *requested_at.saturating_add(cancellation_timelock);

            if is_cancellation_timelock_passed {
                self.state = TransactionState::CancelledOnL2;
            }
        }
    }
}
```

**File:** crates/apollo_l1_events/src/transaction_manager.rs (L116-145)
```rust
    pub fn validate_tx(&mut self, tx_hash: TransactionHash, unix_now: u64) -> ValidationStatus {
        let current_staging_epoch_cloned = self.current_staging_epoch;

        let policy = TransactionRecordPolicy {
            cancellation_timelock: self.config.l1_handler_cancellation_timelock_seconds,
        };

        let validation_status = self.with_record(tx_hash, |record| {
            // If the current time affects the state, update state now.
            record.update_time_based_state(unix_now, policy);
            if !record.is_validatable() {
                match record.state {
                    TransactionState::Committed => {
                        InvalidValidationStatus::AlreadyIncludedOnL2.into()
                    }
                    TransactionState::CancelledOnL2 => {
                        InvalidValidationStatus::CancelledOnL2.into()
                    }
                    TransactionState::Consumed => InvalidValidationStatus::ConsumedOnL1.into(),
                    _ => unreachable!(),
                }
            } else if record.try_mark_staged(current_staging_epoch_cloned) {
                ValidationStatus::Validated
            } else {
                InvalidValidationStatus::AlreadyIncludedInProposedBlock.into()
            }
        });

        validation_status.unwrap_or(InvalidValidationStatus::NotFound.into())
    }
```

**File:** crates/apollo_l1_events/tests/timing_flows.rs (L103-116)
```rust
    // But validate still works, cause cancellation timelock hasn't passed yet for anyone.
    l1_events_provider.start_block(l1_events_provider.current_height, Validate).unwrap();
    assert_eq!(
        l1_events_provider.validate(tx_hash!(2), l1_events_provider.current_height).unwrap(),
        Validated
    );
    assert_eq!(
        l1_events_provider.validate(tx_hash!(3), l1_events_provider.current_height).unwrap(),
        Validated
    );
    assert_eq!(
        l1_events_provider.validate(tx_hash!(4), l1_events_provider.current_height).unwrap(),
        Validated
    );
```

**File:** crates/apollo_node/resources/config_schema.json (L3247-3251)
```json
  "l1_events_provider_config.l1_handler_cancellation_timelock_seconds": {
    "description": "How long to allow a transaction requested for cancellation to be validated against (proposals are banned upon receiving a cancellation request).",
    "privacy": "Public",
    "value": 300
  },
```
