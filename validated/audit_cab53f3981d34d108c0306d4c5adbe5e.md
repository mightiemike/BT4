## Title
Non-deterministic wall-clock transaction timeouts in block-proposal validation can cause signers to disagree on a block's validity - (File: `stackslib/src/net/api/postblock_proposal.rs`)

### Summary
The Nakamoto block-proposal validation endpoint (used by every signer node to check a miner's proposed block before signing it) enforces per-transaction and per-block wall-clock timeouts (`block_proposal_max_tx_execution_time_secs`, `block_proposal_max_tx_analysis_time_secs`, and the overall `block_deadline`) to defend against slow/expensive transactions, mirroring the class of bug fixed by CosmWasm's wasmvm advisory (a malicious contract causing pathological execution time). Because these limits are measured against real (`Instant::now()`) wall-clock time rather than the deterministic, consensus-critical Clarity cost tracker, a contract can be engineered to run near the timeout boundary so that faster/idle nodes finish under the limit while slower/loaded nodes exceed it — producing different `BlockValidateOk`/`BlockValidateReject(ProblematicTransaction)` verdicts on the *same* block across the signer set.

### Finding Description
`ResourceBudget`/`ResourceLimiter` are explicitly documented as non-consensus, wall-clock-based defense-in-depth: "This is NOT related to cost tracking. The latter is consensus-critical and therefore deterministic... During consensus-critical work, the budget MUST be `ResourceBudget::unlimited`" [1](#0-0) .

`NakamotoBlockProposal`'s validation path builds these budgets from `max_tx_execution_time_secs` and `max_tx_analysis_time_secs` and applies them per-transaction while iterating the proposed block's transactions against a real-time `block_deadline = Instant::now() + Duration::from_secs(timeout_secs)`: [2](#0-1) .

If a transaction individually exceeds its per-tx budget it is classified `TransactionResult::Problematic`, which rejects the whole block proposal with `ValidateRejectCode::ProblematicTransaction` and the specific `failed_txid`: [3](#0-2) . If instead the overall `block_deadline` (not tied to any specific tx) is exceeded first, the block is rejected as `InvalidBlock` with no tx flagged: [4](#0-3) .

Both checks measure real elapsed time on the validating machine. This is documented as intentionally non-deterministic ("defense in depth"), which is safe as long as it never affects state-root computation, but it *does* affect the RPC verdict signers rely on to decide whether to sign a block: `handle_block_validate_ok` / `handle_block_validate_reject` drives `mark_pre_committed` vs. `mark_locally_rejected` and broadcast of acceptance/rejection over StackerDB [5](#0-4) . Because different signer nodes run on different hardware, under different load, and even the miner itself is subject to a related but separately-configured `max_execution_time_secs` at mining time [6](#0-5) , a transaction whose actual Clarity execution cost is legitimate (passes the deterministic cost tracker/block budget) but whose wall-clock execution time sits near the configured second-granularity threshold (default 30s, see `mainnet-signer-conf.toml` block_proposal_validation_timeout_ms / stackslib config defaults) can be timed to fall under the limit on some nodes and over it on others.

This breaks the equality that should hold for a block validation verdict: all honest signer nodes validating the identical block bytes should reach the identical accept/reject verdict. Here the verdict instead depends on validator machine speed and concurrent load, which an attacker can amplify by choosing a computation whose runtime clusters right at the second-granularity cutoff (e.g., recursive/looping Clarity calls tuned to run ~29–31s depending on hardware).

### Impact Explanation
This is a minority-triggerable, unprivileged, static-validation divergence: any account able to submit a smart-contract transaction can construct one with borderline wall-clock execution time. The resulting split verdict among signers (some marking the block `ProblematicTransaction`/`InvalidBlock`, others accepting) delays or blocks reaching the ≥70% pre-commit weight threshold needed for a Nakamoto block signature [7](#0-6) , causing repeated rejections, miner retries, and tenure-extend cascades — i.e., the same "slow down block production" outcome as the referenced wasmvm advisory, achieved via a temporary tip/verdict disagreement among signers rather than a state-root mismatch. This maps to the allowed "High - minority-triggerable static-validation divergence... temporary tip disagreement" category.

### Likelihood Explanation
Likely and cheap to trigger: no special privileges, node-operator access, or majority collusion required — a single unprivileged account can broadcast a crafted transaction. Exploitability depends on tuning execution time to straddle a configured second-granularity boundary across heterogeneous signer hardware, which is a realistic and reproducible condition in a decentralized signer set with varying hardware/load (this is precisely the scenario the resource-limiter code's own comments flag as inherently non-deterministic).

### Recommendation
- Avoid using wall-clock-based transaction classification (`ProblematicTransaction`) as a criterion that can produce divergent, broadcast rejection verdicts across signers for otherwise-valid transactions; only use it to bound resource usage of the *local* validating node without emitting reason codes that other signers may treat as authoritative for exclusion.
- Where a timeout-based rejection is unavoidable, ensure it does not translate into a persistent, cross-node "problematic transaction" classification that miners are expected to permanently exclude; instead treat it as a local retry/backoff signal.
- Consider tightening or removing the coupling between wall-clock resource limits and consensus-adjacent reason codes (`ValidateRejectCode::ProblematicTransaction`) that feed directly into signer accept/reject broadcast logic in `postblock_proposal.rs`.

### Proof of Concept
1. Deploy a Clarity contract whose function performs a bounded, cost-tracker-legal but CPU-heavy computation (e.g., large but budget-compliant iteration/hashing) tuned so that on typical validator hardware it runs close to the configured `block_proposal_max_tx_execution_time_secs` (default reflected in `sample/conf/mainnet-miner-conf.toml`'s `max_execution_time_secs = 30`).
2. Submit the transaction so a miner includes it in a proposed Nakamoto block, following `try_mine_tx_with_len` in `postblock_proposal.rs::443`'s validation loop.
3. Signers on faster/idle hosts complete evaluation under `per_tx_max_execution_time` and return `TransactionResult::Success`, leading to `BlockValidateOk`; signers on slower/loaded hosts exceed it and get `TransactionResult::Problematic`, leading to `BlockValidateReject(ProblematicTransaction)`.
4. Observe divergent `BlockResponse` broadcasts (`Accepted` vs `Rejected` with `ProblematicTransaction`) for the identical block hash across the signer set, stalling threshold-based pre-commit accumulation described in `docs/signer-flows.md` and delaying block production/finalization.

### Citations

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

**File:** stackslib/src/net/api/postblock_proposal.rs (L755-771)
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
```

**File:** stackslib/src/net/api/postblock_proposal.rs (L784-824)
```rust
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

**File:** docs/signer-flows.md (L213-221)
```markdown
    IN["BlockValidationResponse<br/>handle_block_validate_response"] --> OK{"verdict?"}
    OK -- "Ok" --> HVO["handle_block_validate_ok:<br/>record validation_time_ms,<br/>skip if already decided"]
    OK -- "Reject" --> HVR["handle_block_validate_reject:<br/>mark_locally_rejected,<br/>broadcast rejection"]:::bad
    HVO --> RECHECK{"still consistent with our DB?<br/>check_block_against_signer_db_state<br/>→ section 7"}
    RECHECK -- no --> REJ["mark_locally_rejected,<br/>handle_block_rejection,<br/>broadcast rejection"]:::bad
    RECHECK -- yes --> PC["mark_pre_committed<br/>(stamps approved_time)"]
    PC --> SEND["send_block_pre_commit<br/>(broadcast over StackerDB)"]
    SEND --> SELF["count our own pre-commit:<br/>handle_block_pre_commit → section 5"]
    TIMEOUT["no answer in time:<br/>check_submitted_block_proposal<br/>frees the slot; next queued proposal<br/>submitted by check_pending_block_validations"]
```

**File:** docs/signer-flows.md (L244-249)
```markdown
    ALREADY -- no --> VALID{"validated ok?<br/>valid = true"}
    VALID -- no --> N2(["wait for validation"])
    VALID -- yes --> TH{"pre-commit weight ≥ 70%?<br/>NakamotoBlockHeader::<br/>compute_voting_weight_threshold"}
    TH -- no --> N3(["wait for more pre-commits"])
    TH -- yes --> RECHECK{"chainstate checks still pass?<br/>check_block_against_signer_db_state<br/>→ section 7"}
    RECHECK -- no --> REJ["mark_locally_rejected,<br/>handle_block_rejection,<br/>broadcast rejection"]:::bad
```

**File:** stackslib/src/config/mod.rs (L3289-3304)
```rust
    /// Defines the maximum execution time (in seconds) allowed for a single contract call
    /// transaction during mining.
    ///
    /// When processing a transaction (contract call or smart contract deployment), if the
    /// execution time exceeds this limit, the transaction processing fails with an
    /// `ExecutionTimeout` error and the transaction is skipped. This prevents
    /// long-running or infinite-loop transactions from blocking block production.
    ///
    /// Mining always enforces a limit; there is no way to disable it. To effectively
    /// "turn it off," set this to a value larger than any tx is expected to take.
    ///
    /// If execution exceeds this limit, the transaction is classified as problematic.
    /// ---
    /// @default: [`DEFAULT_MAX_EXECUTION_TIME_SECS`]
    /// @units: seconds
    pub max_execution_time_secs: u64,
```
