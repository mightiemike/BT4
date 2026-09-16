### Title
Locally-loaded, unsynchronized dynamic fee-config overrides cause honest-node consensus divergence and block-production stalls - ([File: crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs])

### Summary
Each sequencer node independently loads `ContextDynamicConfig` (including `override_l2_gas_price_fri`, `override_l1_gas_price_fri`, `override_l1_data_gas_price_fri`, `override_eth_to_fri_rate`, `min_l2_gas_price_per_height`) from its own local config file via `apollo_config_manager`, and these overrides "take effect immediately" the next time `set_height_and_round` is called on that node — with no coordination, timelock, or height-activation guarantee across the validator set. Because the committed block's `l2_gas_price_fri`/`l1_*_price_fri` fields in `ProposalInit`/`BlockInfo` are derived directly from whichever dynamic config each node has locally loaded at that instant, a rolling/staggered config change (an ordinary, non-malicious operational action, exactly the "bad timing" class in the report) makes the proposer's computed prices diverge from what other honest validators independently compute, so they reject the proposal.

### Finding Description
`ContextDynamicConfig` overrides are re-read from a local file/config source on every height/round transition and applied without any agreed activation height or delay — this is precisely the "admin change takes effect immediately, no advance notice" pattern from the report, transplanted from an admin-fee-setter into a per-node dynamic config loader: [1](#0-0) [2](#0-1) 

These overrides feed directly into gas price fields that become part of the proposed block's committed `ProposalInit`/`BlockInfo`, which every validator independently recomputes from its own dynamic config and compares byte-for-byte against the proposer's values. The project's own regression test demonstrates the exact divergence and resulting rejection when only one node's dynamic config changes between heights/rounds: [3](#0-2) [4](#0-3) 

There is no protocol-level mechanism (activation height, two-phase commit, or timelocked config rollout) ensuring all nodes apply the same dynamic-config values at the same height; the `ConfigManagerRunner` simply diffs and republishes whatever the local file contains on its own poll interval, independent of block height: [5](#0-4) 

The `min_l2_gas_price_per_height` field is height-keyed, showing the developers recognize the need for height-synchronized activation for at least one parameter, but the override fields (`override_l2_gas_price_fri`, `override_l1_gas_price_fri`, `override_l1_data_gas_price_fri`, `override_eth_to_fri_rate`) have no such height gating — they apply unconditionally as soon as the local file is (re)loaded: [6](#0-5) 

### Impact Explanation
If validators/proposers in the active committee do not apply a dynamic-config change (e.g., a routine STRK/USD override rollout, or an operational fee-override push during an incident) at the exact same height, an honest proposer whose local config has already changed will build a block whose gas-price fields the rest of the (not-yet-updated) committee will reject as invalid, per the exact mechanism shown in the test. Since every subsequent proposer in the round-robin may hit the same skew during a staggered rollout, this can force repeated round failures at a given height, stalling block production — i.e., "a network unable to confirm new transactions" for the duration of the rollout skew, which is explicitly an accepted impact category. This is a liveness/availability issue rather than a fund-loss issue, but it is triggered by ordinary node-config timing, not by any malicious actor, matching the report's "purely accidental negative effects... due to unfortunate timing of changes."

### Likelihood Explanation
Likelihood is moderate: dynamic-config rollouts (fee overrides, USD target adjustments) are a normal, expected operational action performed periodically across all sequencer operators, and nothing in the code enforces that all nodes apply such changes atomically at the same height. Any rolling deployment, delayed file sync, or per-operator discretion in when to push a config update creates the exact window demonstrated in the test.

### Recommendation
Add height-gated (or timelocked) activation for all dynamic gas/fee overrides, analogous to the existing `min_l2_gas_price_per_height` pattern: require operators to specify an `activation_height` for each override so that all honest nodes apply the same value from the same agreed height, rather than immediately upon local config reload. Alternatively, derive override activation from a value embedded in already-agreed consensus state (e.g., activate at the next epoch boundary height agreed via consensus) so a staggered rollout cannot cause honest nodes to disagree on a proposal's price fields.

### Proof of Concept
The existing test `change_gas_price_overrides` in `sequencer_consensus_context_test.rs` is itself the PoC: it changes `context.deps.config_manager_client` to return a new `ContextDynamicConfig` with `override_l2_gas_price_fri` set, then calls `set_height_and_round` for the next height/round without changing the incoming `ProposalInit`. The subsequent `validate_proposal` call fails with `Canceled` because the locally-loaded override diverges from the price embedded in the (honest) proposer's `ProposalInit`, confirming that any timing skew in when nodes pick up a dynamic-config change causes honest-node rejection of otherwise-valid proposals: [3](#0-2)

### Citations

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context.rs (L447-470)
```rust
    async fn resolve_fee_target(
        &self,
        timestamp: u64,
        target_atto_usd_per_l2_gas: u128,
    ) -> Option<GasPrice> {
        if let Some(v) = self.config.dynamic_config.override_l2_gas_price_fri {
            SNIP35_FEE_TARGET_FRI.set_lossy(v);
            return Some(GasPrice(v));
        }
        match self.deps.l1_gas_price_provider.get_strk_to_usd_rate(timestamp).await {
            Ok(rate) => {
                let target = compute_fee_target(target_atto_usd_per_l2_gas, rate);
                match target {
                    Some(t) => SNIP35_FEE_TARGET_FRI.set_lossy(t.0),
                    None => warn!("STRK/USD oracle returned zero rate, freezing fee_proposal"),
                }
                target
            }
            Err(e) => {
                warn!("STRK/USD oracle error: {e:?}, freezing fee_proposal");
                None
            }
        }
    }
```

**File:** crates/apollo_consensus_orchestrator/src/fee_market/mod.rs (L71-97)
```rust
/// Compute the next L2 gas price (for the fin or for updating state). Respects override when set.
/// Reporting the bounds is the caller's job, through `NextL2GasPrice::record_clamping`.
pub fn calculate_next_l2_gas_price_for_fin(
    current_l2_gas_price: GasPrice,
    height: BlockNumber,
    l2_gas_used: GasAmount,
    override_l2_gas_price_fri: Option<u128>,
    min_l2_gas_price_per_height: &[PricePerHeight],
    fee_actual: Option<GasPrice>,
) -> NextL2GasPrice {
    if let Some(override_value) = override_l2_gas_price_fri {
        // Operator pin: escapes both bounds by design; each side substitutes its own override.
        info!(
            "L2 gas price ({}) is not updated, remains on override value of {override_value} fri",
            current_l2_gas_price.0
        );
        return NextL2GasPrice { published_price: GasPrice(override_value), bounds: None };
    }
    let gas_target = VersionedConstants::latest_constants().gas_target;
    let config_min = get_min_gas_price_for_height(height, min_l2_gas_price_per_height);
    let cap = l2_gas_price_cap(config_min);

    let snip35_min = fee_actual.map_or(config_min, |fee_actual| max(config_min, fee_actual));
    let effective_min = min(snip35_min, cap);

    let raw_price =
        calculate_next_base_gas_price(current_l2_gas_price, l2_gas_used, gas_target, effective_min);
```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context_test.rs (L1426-1443)
```rust
    let new_dynamic_config = ContextDynamicConfig {
        override_l2_gas_price_fri: Some(ODDLY_SPECIFIC_L2_GAS_PRICE),
        ..Default::default()
    };
    let config_manager_client = make_config_manager_client(new_dynamic_config);
    context.deps.config_manager_client = Some(Arc::new(config_manager_client));

    // Validate block number 1, round 0.
    context.set_height_and_round(HEIGHT_1, ROUND_0).await.unwrap();

    // This should fail, since the gas price is different from the input block info.
    let content_receiver = send_proposal_to_validator_context(&mut context).await;
    let fin_receiver = context
        .validate_proposal(proposal_init(HEIGHT_1, ROUND_0), TIMEOUT, content_receiver)
        .await;
    let proposal_commitment = fin_receiver.await.unwrap_err();
    assert!(matches!(proposal_commitment, Canceled));

```

**File:** crates/apollo_consensus_orchestrator/src/sequencer_consensus_context_test.rs (L1454-1469)
```rust
    let new_dynamic_config = ContextDynamicConfig {
        override_l1_data_gas_price_fri: Some(ODDLY_SPECIFIC_L1_DATA_GAS_PRICE),
        ..Default::default()
    };
    let config_manager_client = make_config_manager_client(new_dynamic_config);
    context.deps.config_manager_client = Some(Arc::new(config_manager_client));

    // This should fail, as we have changed the config, without updating the block info.
    context.set_height_and_round(HEIGHT_1, ROUND_1).await.unwrap();

    let content_receiver = send_proposal_to_validator_context(&mut context).await;
    let fin_receiver = context
        .validate_proposal(proposal_init(HEIGHT_1, ROUND_1), TIMEOUT, content_receiver)
        .await;
    let proposal_commitment = fin_receiver.await.unwrap_err();
    assert!(matches!(proposal_commitment, Canceled));
```

**File:** crates/apollo_config_manager/src/config_manager_runner.rs (L123-154)
```rust
    // TODO(Nadin): Define a proper result type instead of Box<dyn std::error::Error + Send + Sync>
    pub(crate) async fn update_config(
        &mut self,
    ) -> Result<NodeDynamicConfig, Box<dyn std::error::Error + Send + Sync>> {
        let config = load_and_validate_config(self.cli_args.clone(), false).map_err(|e| {
            CONFIG_MANAGER_UPDATE_ERRORS.increment(1);
            error!("ConfigManagerRunner: failed to update config: {e}");
            e
        })?;
        let node_dynamic_config = NodeDynamicConfig::from(&config);

        // Compare the previous and the newly read node dynamic config.
        if self.latest_node_dynamic_config == node_dynamic_config {
            // No change, so no action is needed.
            Ok(node_dynamic_config)
        } else {
            // Log the diff between the latest and the new node dynamic config.
            self.log_config_diff(&self.latest_node_dynamic_config, &node_dynamic_config);
            // Update the latest node dynamic config.
            self.latest_node_dynamic_config = node_dynamic_config.clone();
            match self
                .config_manager_client
                .set_node_dynamic_config(node_dynamic_config.clone())
                .await
            {
                Ok(()) => {
                    info!("Successfully updated dynamic config");
                    Ok(node_dynamic_config)
                }
                Err(e) => Err(format!("Failed to update dynamic config: {:?}", e).into()),
            }
        }
```

**File:** crates/apollo_consensus_orchestrator_config/src/config.rs (L374-401)
```rust
        dump.extend(ser_optional_param(
            &self.override_l2_gas_price_fri,
            0,
            "override_l2_gas_price_fri",
            "Replace the L2 gas price (fri) with this value.",
            ParamPrivacyInput::Public,
        ));
        dump.extend(ser_optional_param(
            &self.override_l1_gas_price_fri,
            0,
            "override_l1_gas_price_fri",
            "Replace the L1 gas price (fri) with this value.",
            ParamPrivacyInput::Public,
        ));
        dump.extend(ser_optional_param(
            &self.override_l1_data_gas_price_fri,
            0,
            "override_l1_data_gas_price_fri",
            "Replace the L1 data gas price (fri) with this value.",
            ParamPrivacyInput::Public,
        ));
        dump.extend(ser_optional_param(
            &self.override_eth_to_fri_rate,
            0,
            "override_eth_to_fri_rate",
            "Replace the Eth-to-Fri conversion rate with this value.",
            ParamPrivacyInput::Public,
        ));
```
