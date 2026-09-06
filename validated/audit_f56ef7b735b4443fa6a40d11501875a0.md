### Title
Wall-clock-bounded transaction validation causes non-deterministic block-proposal verdicts across signers - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
Nakamoto block-proposal validation on the signer's node bounds each transaction's Clarity analysis/execution by real wall-clock time (`max_tx_execution_time_secs`, `max_tx_analysis_time_secs`) and by an overall `timeout_secs` deadline. Because this is measured with `Instant::now()` against machine-local wall-clock time rather than the deterministic, consensus-critical cost tracker, the exact same block proposal and transaction can be accepted by one signer and rejected (as `BadTransaction`/`ProblematicTransaction`/`InvalidBlock`) by another, purely as a function of that signer's hardware speed/load. This mirrors the packet-forward-middleware bug class: identical input producing divergent, node-local error/success outcomes that different validators disagree on.

### Finding Description
`BlockProposal::validate` explicitly uses wall-clock timers to bound per-transaction execution: [1](#0-0) 

For every transaction, if the transaction's own execution/analysis exceeds `per_tx_max_execution_time` / `per_tx_max_analysis_time` (both real-time `Duration`s tracked via `Instant::now()` inside `ResourceLimiter`), or if the whole-block deadline (`block_deadline`, also `Instant::now() + Duration::from_secs(timeout_secs)`) is exceeded, the block is rejected outright with a `BadTransaction`/`ProblematicTransaction`/`InvalidBlock` verdict and a `failed_txid`: [2](#0-1) 

The resource-limiter module itself documents that this timing mechanism is intentionally *not* part of the deterministic, consensus-critical cost tracker, and is only meant to be used off the commit/replay path: [3](#0-2) [4](#0-3) 

The `TimeTracker` implementation confirms the check is a literal `Instant::elapsed() >= max_duration` wall-clock comparison, with no relation to the deterministic cost-tracking units used elsewhere in the codebase: [5](#0-4) 

Because every signer independently calls `stacks-node`'s `/v3/block_proposal` endpoint (`validate`) on its own hardware, with its own CPU load, disk I/O, and JIT/interpreter warm-up state, a transaction whose true execution time sits close to the configured budget (default `block_proposal_max_tx_execution_time_secs = 30s`, per the CHANGELOG entry introducing this option) can legitimately finish under budget on a fast/idle signer node and exceed the budget on a slow/loaded signer node. The rejecting signer broadcasts a `BlockRejection` while the accepting signer proceeds toward `mark_locally_accepted` / pre-commit / signature, per the flow in `handle_block_validate_ok`: [6](#0-5) 

This is the same bug class as GHSA-w6rp-vxj2-fjhr: a non-deterministic error path (there, IBC ack error codes differing across validators; here, timeout-triggered validation errors differing across signers) that causes different network participants to reach different verdicts on the *same* input.

### Impact Explanation
This does not corrupt the deterministic state root computed by `append_block`/`clarity_tx.seal()` (that path always uses `ResourceBudget::unlimited()` per the module's own invariant), so it cannot directly fork the chain's committed state. However, it can cause:
- A minority of signers to reject an objectively valid block proposal while the majority accepts it — a genuine "static-validation divergence" among signers on identical input, matching the rules' High-severity bucket ("a minority-triggerable ... static-validation divergence ... temporary tip disagreement").
- If enough signers happen to be slow/loaded when a borderline-cost transaction is proposed, the block can fail to reach the 70% pre-commit/signature threshold in time, forcing miners to reproduce, drop the transaction, or extend the tenure — a transient stall that is externally indistinguishable from a chain halt to end users, even though it self-heals once the miner retries without the borderline transaction.
- Because the trigger is a single unprivileged, ordinary transaction (any user can submit a contract call whose cost is engineered to sit near the timeout boundary), no majority or privileged access is required — only "minority" signer hardware variance is needed to produce disagreement.

### Likelihood Explanation
Moderate-to-high. `max_tx_execution_time_secs`/analysis time and the overall `timeout_secs` are wall-clock, not deterministic-cost, bounds, so any signer running on materially slower or more loaded hardware than the median is naturally exposed to different verdicts for transactions that happen to execute close to the configured ceiling. An attacker does not need to compromise any signer; they only need to submit (or convince a miner to include) a transaction whose real Clarity execution time is close to the per-tx budget, which is straightforward to construct given expensive but "legal" Clarity operations (e.g., deep recursion/looping via `fold`/`map`/`filter`, or heavy string/buffer manipulation) that are within cost-tracker limits but consume execution wall time near the configured threshold.

### Recommendation
Tie the per-transaction/per-block "problematic" bound during proposal validation to the deterministic Clarity cost-tracker units (which are already consensus-critical and identical across nodes) instead of, or in addition to, wall-clock time; or, if wall-clock bounding must remain for defense-in-depth against runaway execution, treat a timeout as inconclusive (retry / fall back to a purely cost-based re-check) rather than an immediate, broadcast-worthy rejection, and require multiple signers/attempts to agree before treating a borderline transaction as definitively problematic.

### Proof of Concept
1. Construct a Clarity contract-call transaction whose execution time (as measured by `Instant::now()`-based wall-clock, not cost units) is close to the configured `block_proposal_max_tx_execution_time_secs` (default 30s) — e.g., a bounded loop/fold operation whose real wall time on a "slow" node exceeds 30s but on a "fast/idle" node completes in well under 30s, while both stay within normal Clarity cost-tracker limits.
2. Have a miner include this transaction in a Nakamoto block proposal and submit it to the signer set via `/v3/block_proposal`.
3. On signer nodes under typical load/hardware variance, `BlockProposal::validate` (stackslib/src/net/api/postblock_proposal.rs, lines 731-824) will time the transaction with `Instant::now()`; slower/loaded signers hit `per_tx_max_execution_time` and return `BadTransaction`/`ProblematicTransaction` (`failed_txid` set), while faster/idle signers complete normally and return `BlockValidateOk`.
4. Observe divergent `BlockRejection`/`BlockValidateOk` verdicts broadcast by different signers for the identical block/transaction, verifiable via the existing `handle_block_validate_ok`/`handle_block_validate_reject` flow in `stacks-signer/src/v0/signer.rs`.

Note: I was not able to fully trace every downstream consequence of a split signer verdict (e.g., exact tenure-extension/reproposal behavior under `stacks-node/src/tests/signer/v0/failed_txs.rs` and `problematic_txs.rs`) within the available index; a Devin session with full repo access could run/extend those integration tests to empirically confirm the magnitude of the resulting tip disagreement.

### Citations

**File:** stackslib/src/net/api/postblock_proposal.rs (L731-753)
```rust
        let block_deadline = Instant::now() + Duration::from_secs(timeout_secs);
        let per_tx_max_execution_time = Duration::from_secs(max_tx_execution_time_secs);
        // Bound the analysis phase during proposal validation by the
        // dedicated per-tx analysis budget, independently of the eval budget above.
        let per_tx_max_analysis_time = Duration::from_secs(max_tx_analysis_time_secs);
        let mut receipts_total = 0u64;

        let max_tx_mem_bytes_opt = if max_tx_mem_bytes > 0 {
            Some(max_tx_mem_bytes)
        } else {
            None
        };
        let resource_budgets = TransactionResourceBudgets::new()
            .with_analysis_budget(
                ResourceBudget::new()
                    .with_max_duration(Some(per_tx_max_analysis_time))
                    .with_max_memory_use(max_tx_mem_bytes_opt),
            )
            .with_execution_budget(
                ResourceBudget::new()
                    .with_max_duration(Some(per_tx_max_execution_time))
                    .with_max_memory_use(max_tx_mem_bytes_opt),
            );
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L755-824)
```rust
        for (i, tx) in self.block.txs.iter().enumerate() {
            // Enforce the overall block validation budget between txs. A tx
            // running over its own per-tx limit is the tx's fault and is
            // handled below; running out of overall budget is the block's
            // fault and shouldn't flag any specific tx as problematic.
            if Instant::now() >= block_deadline {
                warn!(
                    "Rejected block proposal";
                    "reason" => "Block validation timed out",
                    "next_tx_index" => i,
                );
                return Err(BlockValidateRejectReason {
                    reason: format!("Block validation timed out before tx {i} could be processed"),
                    reason_code: ValidateRejectCode::InvalidBlock,
                    failed_txid: None,
                });
            }

            let tx_len = tx.tx_len();

            let tx_result = builder.try_mine_tx_with_len(
                &mut tenure_tx,
                tx,
                tx_len,
                &BlockLimitFunction::NO_LIMIT_HIT,
                &resource_budgets,
                &mut receipts_total,
            );

            let reason = match tx_result {
                TransactionResult::Success(success_result) => {
                    let all_events_valid = success_result
                        .receipt
                        .events
                        .iter()
                        .all(|event| is_event_pox_addr_valid(mainnet, event));
                    if !all_events_valid {
                        Some((
                            format!("Problematic tx {i}: contains invalid pox address"),
                            ValidateRejectCode::ProblematicTransaction,
                        ))
                    } else {
                        None
                    }
                }
                TransactionResult::Skipped(s) => Some((
                    format!("tx {i} skipped: {}", s.error),
                    ValidateRejectCode::BadTransaction,
                )),
                TransactionResult::ProcessingError(e) => Some((
                    format!("Error processing tx {i}: {}", e.error),
                    ValidateRejectCode::BadTransaction,
                )),
                TransactionResult::Problematic(p) => Some((
                    format!("Problematic tx {i}: {}", p.error),
                    ValidateRejectCode::ProblematicTransaction,
                )),
            };
            if let Some((reason, reject_code)) = reason {
                warn!(
                    "Rejected block proposal";
                    "reason" => %reason,
                    "tx" => ?tx,
                );
                return Err(BlockValidateRejectReason {
                    reason,
                    reason_code: reject_code,
                    failed_txid: Some(tx.txid()),
                });
            }
```

**File:** clarity/src/vm/resource_limiter.rs (L24-41)
```rust
/// Tracks wall-clock time spent in a single execution phase of one transaction
/// (Clarity evaluation *or* contract analysis) and signals when a configured
/// deadline has elapsed.
///
/// [`TimeTracker::NoTracking`] is the deterministic-replay / no-limit case (it must be used on
/// the commit/replay path so consensus stays deterministic).
///
/// [`TimeTracker::MaxTime`] is used only on the non-consensus voting paths:
/// block assembly (mining) and block-proposal validation (signers) to bound the time
/// a single transaction can spend.
#[derive(Clone, Copy)]
enum TimeTracker {
    NoTracking,
    MaxTime {
        start_time: Instant,
        max_duration: Duration,
    },
}
```

**File:** clarity/src/vm/resource_limiter.rs (L77-101)
```rust
    /// Returns and error if a deadline is configured and has elapsed. Always
    /// Ok(()) for `NoTracking`.
    pub fn check_not_expired(&self) -> Result<(), String> {
        match self {
            Self::NoTracking => Ok(()),
            Self::MaxTime {
                start_time,
                max_duration,
            } => {
                let elapsed = start_time.elapsed();
                // semantically `>` would be more correct than `>=`, but there
                // are a few tests that assume "zero always expires", and since
                // it makes not practical difference otherwise, we use `>=`.
                if elapsed >= *max_duration {
                    Err(format!(
                        "Elapsed time of {} ms exceeds budget of {} ms.",
                        elapsed.as_millis(),
                        max_duration.as_millis()
                    ))
                } else {
                    Ok(())
                }
            }
        }
    }
```

**File:** clarity/src/vm/resource_limiter.rs (L192-199)
```rust
/// This is NOT related to cost tracking. The latter is consensus-critical and therefore
/// deterministic. The purpose of the [`ResourceBudget`] is defense-in-depth: If
/// a bug in clarity evaluation or analysis causes a long runtime or a huge amount
/// of memory being used, the miner will not include it in a block, and the signer
/// will reject the block as problematic.
///
/// During consensus-critical work, the budget MUST be [`ResourceBudget::unlimited`]
/// to ensure determinism.
```

**File:** stacks-signer/src/v0/signer.rs (L1941-1984)
```rust
        if !block_info.check_static_valid_block() {
            debug!("{self}: Block is syntatically invalid; will not store");
            return;
        }

        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            // The signer db state has changed. We no longer view this block as valid. Override the validation response.
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
        } else {
            if let Err(e) = block_info.mark_pre_committed() {
                // The block may have reached enough signatures before we validated the block so should fail to mark pre-committed
                // but still call to make sure the timestamps and validity are updated correctly.
                if !block_info.has_reached_consensus()
                    && block_info.state != BlockState::LocallyAccepted
                {
                    warn!("{self}: Failed to mark block as approved: {e:?}",);
                    return;
                }
            }

            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.send_block_pre_commit(signer_signature_hash.clone());
            // have to save the signature _after_ the block info
            let address = self.stacks_address.clone();
            self.handle_block_pre_commit(
                stacks_client,
                sortition_state,
                &address,
                signer_signature_hash,
            );
        }
```
