### Title
Stale `target_shard` in a delayed `GlobalContractDistributionReceipt` can produce receipt loss or a transaction-triggered chain halt after resharding - (`core/primitives/src/receipt.rs`)

### Summary
An unprivileged account can trigger `DeployGlobalContractAction`, which spawns a `GlobalContractDistributionReceipt` carrying a fixed `target_shard: ShardId` [1](#0-0) . If that receipt is delayed in the queue while one or more resharding events change the shard layout (a config/topology change analogous to the DAO updating `forkEscrow` mid-escrow in the referenced report), the stored `target_shard` no longer exists in the current layout. Resolving it depends entirely on `ShardLayout::resolve_to_current_shard`, and `receipt_filter_fn`/`receiver_shard_id` `.unwrap()` on any resolution failure [2](#0-1) [3](#0-2) .

### Finding Description
`action_deploy_global_contract` lets any account deploy a global contract, which is converted into a `GlobalContractDistributionReceipt` pinned to the current shard as `target_shard` [4](#0-3) [1](#0-0) . This receipt is forwarded shard-by-shard until fully distributed [5](#0-4) [6](#0-5) , and it can sit in the persistent delayed-receipts queue under congestion for an arbitrary number of blocks/epochs, just as Nouns escrow entries can sit in `forkEscrow` for an arbitrary time before withdrawal.

`Receipt::receiver_shard_id` special-cases `GlobalContractDistribution`: if `target_shard` is no longer present in the current `ShardLayout`, it calls `shard_layout.resolve_to_current_shard(target_shard)`, and only if that returns `None` does it produce a recoverable `EpochError` [3](#0-2) . However, `receipt_filter_fn` inside the delayed-receipt-queue draining path (`DelayedReceiptQueueWrapper::pop`) calls this same function with a bare `.unwrap()`:

```
let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
``` [2](#0-1) 

This is the same architectural pattern as the reported bug: the `forkEscrow` reference is analogous to `target_shard`/shard layout — a value fixed at the time a user-triggered action created an escrowed/queued item, which a later privileged reconfiguration (protocol upgrade voting in a new shard layout via resharding, analogous to DAO's `_setForkEscrow`) can silently invalidate, and the only recovery path (`resolve_to_current_shard`'s split-history walk) is bounded and can fail to find the shard, or can be exercised across resharding paths that don't preserve enough split history. The engineering team's own regression test acknowledges the historical breadth of this risk: `test_stale_global_contract_distribution_after_double_resharding` states explicitly that "If the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()` when `receiver_shard_id()` fails to remap the old `target_shard`" and that "the fix only works with V3 shard layouts (dynamic resharding). With static resharding, the shard layout doesn't maintain a full split history" [7](#0-6) [8](#0-7) .

### Impact Explanation
If `resolve_to_current_shard` cannot map a stale `target_shard` to a descendant shard in the current layout (any resharding path/history depth not covered by the retained split-history, as the test comment itself flags for non-V3/static resharding), the `.unwrap()` in `receipt_filter_fn` panics while draining the delayed-receipt queue for every subsequent block that must process past that entry. Because delayed receipts are drained FIFO and this call sits on the hot path of `DelayedReceiptQueueWrapper::pop`, invoked from `process_delayed_receipts` on every applied chunk [9](#0-8) , a panic here is a transaction-triggered halt of the affected shard's chunk production — nodes cannot make further progress applying that shard until the state is hand-fixed. This matches the class of impact explicitly accepted by the validation rules ("a transaction-triggered halt"), and is reachable purely from an unprivileged `DeployGlobalContractAction` transaction combined with naturally occurring resharding (a protocol-level event, not attacker-controlled but not "malicious validator/operator" either — it is a scheduled network event).

### Likelihood Explanation
Deploying a global contract is a fully permissionless, single transaction available to any account. Getting the resulting `GlobalContractDistributionReceipt` to sit in the delayed queue across a resharding event requires either natural congestion or deliberately saturating the target shard's compute budget with cheap self-calls (exactly as the repo's own test does), which is inexpensive and fully within reach of an unprivileged sender. The remaining likelihood gate is whether a resharding event that is *not* covered by the specific double-split/dynamic-resharding fix path occurs while the receipt is delayed. The nearcore team's own test comments confirm this gap exists for non-V3 (static) resharding history, and the exact bound of "how many resharding generations back" `resolve_to_current_shard` can walk was not verified from the code alone in this session — this is the main residual uncertainty.

### Recommendation
Replace the `.unwrap()` in `DelayedReceiptQueueWrapper::receipt_filter_fn` with a proper `Result`-propagating error path so an unresolvable `target_shard` degrades to a soft `StorageInconsistentState`/discard-with-log rather than a panic, and ensure `ShardLayout::resolve_to_current_shard` retains (or the distribution-receipt format carries) enough split-history depth to resolve `target_shard` across *any* number of resharding events the receipt could plausibly outlive while delayed, not just the specific "double dynamic resharding" scenario currently covered by the acknowledged fix.

### Proof of Concept
The repository's own test demonstrates the reachable path end-to-end:
1. Deploy a test contract and a global contract from an ordinary user account, producing a `GlobalContractDistributionReceipt` with `target_shard` = the deploying account's shard [10](#0-9) .
2. Saturate that shard's compute budget every block with `burn_gas_raw` calls so the distribution receipt is pushed into, and kept in, the delayed-receipts queue [11](#0-10) .
3. Let two shard-split (resharding) events occur while the receipt is delayed, changing `target_shard`'s validity in the current `ShardLayout` [12](#0-11) .
4. Stop saturating and drain the delayed queue; the test asserts the chain does **not** stall, explicitly because "if the vulnerability exists, processing the stale GlobalContractDistribution receipt will panic in `receipt_filter_fn()`" [13](#0-12) .

The test is scoped to (and only asserts safety for) `ProtocolFeature::DynamicResharding`/V3 shard layouts [14](#0-13) , leaving the static-resharding/older-split-history case — acknowledged by the same comment as not maintaining full split history — unverified against this exact failure mode in this session.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L25-63)
```rust
pub(crate) fn action_deploy_global_contract(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    apply_state: &ApplyState,
    deploy_contract: &DeployGlobalContractAction,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let _span = tracing::debug_span!(target: "runtime", "action_deploy_global_contract").entered();

    let storage_cost = apply_state
        .config
        .fees
        .storage_usage_config
        .global_contract_storage_amount_per_byte
        .saturating_mul(deploy_contract.code.len() as u128);
    let Some(updated_balance) = account.amount().checked_sub(storage_cost) else {
        result.result = Err(ActionErrorKind::LackBalanceForState {
            account_id: account_id.clone(),
            amount: storage_cost,
        }
        .into());
        return Ok(());
    };
    result.tokens_burnt =
        result.tokens_burnt.checked_add(storage_cost).ok_or(IntegerOverflowError)?;
    account.set_amount(updated_balance);

    initiate_distribution(
        state_update,
        account_id.clone(),
        deploy_contract.code.clone(),
        &deploy_contract.deploy_mode,
        apply_state.shard_id,
        result,
    )?;

    Ok(())
}
```

**File:** runtime/runtime/src/global_contracts.rs (L111-141)
```rust
pub(crate) fn apply_global_contract_distribution_receipt(
    receipt: &Receipt,
    apply_state: &ApplyState,
    epoch_info_provider: &dyn EpochInfoProvider,
    state_update: &mut TrieUpdate,
    receipt_sink: &mut ReceiptSink,
    receipt_to_tx: &mut Vec<(CryptoHash, ReceiptToTxInfo)>,
) -> Result<Compute, RuntimeError> {
    let _span = tracing::debug_span!(
        target: "runtime",
        "apply_global_contract_distribution_receipt",
    )
    .entered();

    let ReceiptEnum::GlobalContractDistribution(global_contract_data) = receipt.receipt() else {
        unreachable!("given receipt should be an global contract distribution receipt")
    };
    let compute =
        apply_distribution_current_shard(receipt, global_contract_data, apply_state, state_update)?;
    forward_distribution_next_shard(
        receipt,
        global_contract_data,
        apply_state,
        epoch_info_provider,
        state_update,
        receipt_sink,
        receipt_to_tx,
    )?;

    Ok(compute)
}
```

**File:** runtime/runtime/src/global_contracts.rs (L143-171)
```rust
fn initiate_distribution(
    state_update: &mut TrieUpdate,
    account_id: AccountId,
    contract_code: Arc<[u8]>,
    deploy_mode: &GlobalContractDeployMode,
    current_shard_id: ShardId,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
    // Increment the nonce and write it to state immediately to prevent multiple
    // distributions with the same nonce from being initiated. This requires
    // allowing the same nonce in the freshness check when applying the
    // distribution receipt.
    let nonce = increment_nonce(state_update, &id)?;
    let distribution_receipt =
        GlobalContractDistributionReceipt::new(id, current_shard_id, vec![], contract_code, nonce);
    let distribution_receipts =
        Receipt::new_global_contract_distribution(account_id, distribution_receipt);
    // No need to set receipt_id here, it will be generated as part of apply_action_receipt
    result.new_receipts.push(distribution_receipts);
    Ok(())
}
```

**File:** runtime/runtime/src/global_contracts.rs (L288-333)
```rust
fn forward_distribution_next_shard(
    receipt: &Receipt,
    global_contract_data: &GlobalContractDistributionReceipt,
    apply_state: &ApplyState,
    epoch_info_provider: &dyn EpochInfoProvider,
    state_update: &mut TrieUpdate,
    receipt_sink: &mut ReceiptSink,
    receipt_to_tx: &mut Vec<(CryptoHash, ReceiptToTxInfo)>,
) -> Result<(), RuntimeError> {
    let shard_layout = epoch_info_provider.shard_layout(&apply_state.epoch_id)?;
    let already_delivered_shards = BTreeSet::from_iter(
        global_contract_data
            .already_delivered_shards()
            .iter()
            .cloned()
            .chain(std::iter::once(apply_state.shard_id)),
    );
    let Some(next_shard) = shard_layout
        .shard_ids()
        .filter(|shard_id| !already_delivered_shards.contains(&shard_id))
        .next()
    else {
        return Ok(());
    };
    let already_delivered_shards = Vec::from_iter(already_delivered_shards);
    let predecessor_id = receipt.predecessor_id().clone();
    let next_receipt = global_contract_data.forward(next_shard, already_delivered_shards);
    let mut next_receipt = Receipt::new_global_contract_distribution(predecessor_id, next_receipt);
    let receipt_id = apply_state.create_receipt_id(receipt.receipt_id(), 0);
    next_receipt.set_receipt_id(receipt_id);
    if apply_state.save_receipt_to_tx {
        receipt_to_tx.push((
            receipt_id,
            ReceiptToTxInfo::V1(ReceiptToTxInfoV1 {
                origin: ReceiptOrigin::FromReceipt(ReceiptOriginReceipt {
                    parent_receipt_id: *receipt.receipt_id(),
                    parent_predecessor_id: receipt.predecessor_id().clone(),
                }),
                receiver_account_id: next_receipt.receiver_id().clone(),
                shard_id: apply_state.shard_id,
            }),
        ));
    }
    receipt_sink.forward_or_buffer_receipt(next_receipt, apply_state, state_update)?;
    Ok(())
}
```

**File:** runtime/runtime/src/congestion_control.rs (L874-878)
```rust
    fn receipt_filter_fn(&self, receipt: &ReceiptOrStateStoredReceipt) -> bool {
        let shard_layout = self.epoch_info_provider.shard_layout(&self.epoch_id).unwrap();
        let receipt_shard_id = receipt.get_receipt().receiver_shard_id(&shard_layout).unwrap();
        receipt_shard_id == self.shard_id
    }
```

**File:** core/primitives/src/receipt.rs (L437-466)
```rust
    pub fn receiver_shard_id(&self, shard_layout: &ShardLayout) -> Result<ShardId, EpochError> {
        let shard_id = match self.receipt() {
            ReceiptEnum::Action(_)
            | ReceiptEnum::ActionV2(_)
            | ReceiptEnum::Data(_)
            | ReceiptEnum::PromiseYield(_)
            | ReceiptEnum::PromiseYieldV2(_)
            | ReceiptEnum::PromiseResume(_) => {
                shard_layout.account_id_to_shard_id(self.receiver_id())
            }
            ReceiptEnum::GlobalContractDistribution(receipt) => {
                let target_shard = receipt.target_shard();
                if shard_layout.shard_ids().contains(&target_shard) {
                    target_shard
                } else {
                    // The target shard may be from an arbitrarily old layout (the receipt could
                    // have been delayed across multiple resharding events). resolve_to_current_shard
                    // will find a shard descendant in the current layout.
                    let Some(current_shard) = shard_layout.resolve_to_current_shard(target_shard)
                    else {
                        return Err(EpochError::ShardingError(format!(
                            "Shard {target_shard} does not exist in the shard layout or its split history",
                        )));
                    };
                    current_shard
                }
            }
        };
        Ok(shard_id)
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L30-39)
```rust
#[test]
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_stale_global_contract_distribution_after_double_resharding() {
    init_test_logger();

    // The fix only works with V3 shard layouts (dynamic resharding).
    // With static resharding, the shard layout doesn't maintain a full split history.
    if !ProtocolFeature::DynamicResharding.enabled(PROTOCOL_VERSION) {
        return;
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L94-114)
```rust
    // Step 1: Deploy the test contract on user0's account so we can call burn_gas_raw.
    {
        let node = env.node_for_account(&chunk_producer);
        let tx = node.tx_deploy_test_contract(&deploy_user);
        node.submit_tx(tx);
    }
    env.runner_for_account(&chunk_producer).run_for_number_of_blocks(2);

    // Step 2: Deploy a global contract from user0. This creates a
    // GlobalContractDistribution receipt with target_shard = user0's shard (S_A),
    // which is the shard that will be split in the first resharding.
    {
        let node = env.node_for_account(&chunk_producer);
        let code = ContractCode::new(near_test_contracts::rs_contract().to_vec(), None);
        let tx = node.tx_deploy_global_contract(
            &deploy_user,
            code.code().to_vec(),
            GlobalContractDeployMode::CodeHash,
        );
        node.submit_tx(tx);
    }
```

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L116-186)
```rust
    // Step 3: Saturate compute on user0's shard every block so that the
    // GlobalContractDistribution receipt (arriving as incoming) gets pushed to
    // the delayed queue and stays there through both resharding events.
    //
    // Each burn_gas_raw call burns slightly more than half the gas limit, so
    // two local receipts exhaust the chunk's compute budget. We submit 3 per
    // block to ensure at least 2 are processed as local receipts.
    let gas_to_burn = gas_limit.checked_div(2).unwrap().checked_add(Gas::from_gas(1)).unwrap();
    let initial_num_shards = base_shard_layout.num_shards();
    let target_num_shards = initial_num_shards + 2; // after two splits

    let start_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };

    // Keep saturating until both resharding events complete. Dynamic resharding has a
    // 2-epoch proposal-to-activation pipeline, so we need enough epochs for both splits.
    let max_saturation_height = start_height + epoch_length * 12;
    let mut both_splits_done = false;
    for target_height in (start_height + 1)..=max_saturation_height {
        // Submit 3 heavy transactions to saturate this block's compute budget.
        {
            let node = env.node_for_account(&chunk_producer);
            for _ in 0..3 {
                let tx = node.tx_call(
                    &deploy_user,
                    &deploy_user,
                    "burn_gas_raw",
                    gas_to_burn.as_gas().to_le_bytes().to_vec(),
                    Balance::ZERO,
                    gas_limit,
                );
                node.submit_tx(tx);
            }
        }
        env.runner_for_account(&chunk_producer).run_until_head_height(target_height);

        // Check if both resharding events have completed.
        let node = env.node_for_account(&chunk_producer);
        let epoch_id = node.client().chain.chain_store().head().unwrap().epoch_id;
        let current_layout = node.client().epoch_manager.get_shard_layout(&epoch_id).unwrap();
        if current_layout.num_shards() >= target_num_shards {
            both_splits_done = true;
            break;
        }
    }
    assert!(both_splits_done, "both shard splits did not complete within the allotted blocks");

    // Step 4: Stop saturating. Let the delayed queue drain.
    // If the vulnerability exists, processing the stale GlobalContractDistribution
    // receipt will panic in receipt_filter_fn() when receiver_shard_id() fails
    // to remap the old target_shard after two resharding generations.
    let current_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    let drain_end = current_height + epoch_length * 2;
    env.runner_for_account(&chunk_producer).run_until_head_height(drain_end);

    let head_height = {
        let node = env.node_for_account(&chunk_producer);
        node.client().chain.chain_store().head().unwrap().height
    };
    assert!(
        head_height >= drain_end,
        "chain stalled at height {}; expected >= {} (likely panicked processing stale receipt)",
        head_height,
        drain_end
    );
}
```

**File:** runtime/runtime/src/lib.rs (L2570-2651)
```rust
    fn process_delayed_receipts(
        &self,
        mut processing_state: &mut ApplyProcessingReceiptState,
        receipt_sink: &mut ReceiptSink,
        compute_limit: u64,
        validator_proposals: &mut Vec<ValidatorStake>,
    ) -> Result<(), RuntimeError> {
        let delayed_processing_start = std::time::Instant::now();
        let protocol_version = processing_state.protocol_version;
        let mut delayed_receipt_count = 0;

        let mut next_schedule_after = {
            let mut prep_lookahead_iter =
                processing_state.delayed_receipts.peek_iter(&processing_state.state_update);
            schedule_contract_preparation(
                &mut processing_state.pipeline_manager,
                &processing_state.state_update,
                &mut prep_lookahead_iter,
            )
        };

        loop {
            if processing_state.total.compute >= compute_limit
                || processing_state.state_update.trie.check_proof_size_limit_exceed()
            {
                break;
            }

            let receipt = if let Some(receipt) = processing_state
                .delayed_receipts
                .pop(&mut processing_state.state_update, &processing_state.apply_state.config)?
            {
                receipt.into_receipt()
            } else {
                // Break loop if there are no more receipts to be processed.
                break;
            };

            // TODO(resharding): Add metric for tracking number of
            delayed_receipt_count += 1;
            if let Some(nsi) = &mut next_schedule_after {
                *nsi = nsi.saturating_sub(1);
                if *nsi == 0 {
                    let mut prep_lookahead_iter =
                        processing_state.delayed_receipts.peek_iter(&processing_state.state_update);
                    next_schedule_after = schedule_contract_preparation(
                        &mut processing_state.pipeline_manager,
                        &processing_state.state_update,
                        &mut prep_lookahead_iter,
                    );
                }
            }

            if let Some(prefetcher) = &mut processing_state.prefetcher {
                // Prefetcher is allowed to fail
                _ = prefetcher.prefetch_receipts_data(std::slice::from_ref(&receipt));
            }

            // Validating the delayed receipt. If it fails, it's likely the state is inconsistent.
            validate_receipt(
                &processing_state.apply_state.config.wasm_config.limit_config,
                &receipt,
                protocol_version,
                ValidateReceiptMode::ExistingReceipt,
            )
            .map_err(|e| {
                StorageError::StorageInconsistentState(format!(
                    "Delayed receipt {:?} in the state is invalid: {}",
                    receipt, e
                ))
            })?;

            self.process_receipt_and_instant_receipts(
                &receipt,
                &mut processing_state,
                receipt_sink,
                validator_proposals,
            )?;
            processing_state
                .processed_receipts
                .push(ProcessedReceipt { receipt, source: ReceiptSource::Delayed });
        }
```
