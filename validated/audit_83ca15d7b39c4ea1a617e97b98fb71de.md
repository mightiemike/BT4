### Title
Wall-clock `ResourceBudget` timeout in block-proposal validation lets an unprivileged transaction author force signers to disagree on the same block - ([File: stackslib/src/net/api/postblock_proposal.rs])

### Summary
`postblock_proposal.rs`'s block-proposal validation replays every transaction in a proposed block under a `TransactionResourceBudgets` that is bounded by wall-clock `Duration`s (`block_proposal_max_tx_execution_time_secs`, `block_proposal_max_tx_analysis_time_secs`), which are explicitly documented as non-deterministic, machine-dependent limits used only on the signer/miner validation path [1](#0-0) . Because two signer nodes with different CPU speed/load can legitimately produce different `elapsed >= max_duration` verdicts for the identical transaction, an unprivileged user can construct a contract call whose runtime straddles this boundary, causing some signers to classify the transaction (and hence the whole block proposal) as `Problematic`/rejected while others accept it - a validation verdict divergence across nodes for the same proposed block. This mirrors the `tryCatch`/EIP-150 "rule of 1/64th" bug class: a resource metric checked dynamically at runtime, rather than pinned to a deterministic, consensus-safe value, determines whether execution is treated as having "failed," and an attacker can steer inputs to land exactly on that non-deterministic boundary.

### Finding Description
`TransactionResourceBudgets::from_settings`/the proposal-validation code builds an execution/analysis budget from wall-clock durations sourced from node configuration (`max_execution_time_secs`, `block_proposal_max_tx_execution_time_secs`, etc.) [2](#0-1) [3](#0-2) . When validating a proposed Nakamoto block, `postblock_proposal.rs` constructs a `ResourceBudget` from these durations and feeds it into `TransactionResourceBudgets` used for each transaction's execution and analysis phases [4](#0-3) .

Deep in the interpreter, `check_interpreter_resource_usage` calls `ResourceLimiter::check_not_exceeded`, which is purely `Instant::now()`/`elapsed()` based: `if elapsed >= *max_duration { Err(...) }` [5](#0-4) , and this check is invoked on every single `eval()` call [6](#0-5) . The `ResourceBudget` doc explicitly states this "is NOT related to cost tracking. The latter is consensus-critical and therefore deterministic... During consensus-critical work, the budget MUST be `ResourceBudget::unlimited()` to ensure determinism" [7](#0-6)  - i.e., the codebase itself acknowledges this metric is inherently non-deterministic and only safe to use off the commit path.

If a transaction's true execution time falls in a narrow window straddling `max_duration` on different hardware (slower vs faster signer nodes, or a signer under momentary load from other work), `ExecutionResourceBudgetExceeded` fires on some nodes and not others. `postblock_proposal.rs` treats a resource-budget-exceeded transaction as "problematic," which causes that node to reject the block proposal outright with `BlockValidateRejectReason` blaming the offending txid, per the test `test_block_proposal_validation_execution_time_expired_blames_tx` [8](#0-7) . A different (e.g. faster or idle) signer validating the exact same proposal executes the same transaction within budget and signs approval. The equality broken is: "all signers reach the same validation verdict for an identical block proposal" - the same block is simultaneously a valid candidate for signature by some signers and a rejected candidate for others.

### Impact Explanation
This is a minority-triggerable (single unprivileged transaction author, no relayer/majority needed) divergence in signer validation verdicts for the same block proposal, matching the explicitly in-scope bucket "a validation verdict two nodes disagree on." Depending on how signature weight is distributed across fast vs. slow/loaded signer nodes at the time of proposal, this can: (a) cause temporary tip disagreement / delayed block confirmation as signers split their votes, and (b) be repeated by the attacker (crafting new borderline-timing transactions) to grief specific miners/signers whose hardware profile is known, without ever needing to compromise a majority of stake or any key.

### Likelihood Explanation
An attacker only needs to submit an ordinary transaction (contract-call or contract-deploy) whose evaluation time is tunable near the configured `max_tx_execution_time_secs`/`block_proposal_max_tx_analysis_time_secs` threshold (e.g., via a loop with an attacker-chosen bound, similar in spirit to the `TestTryCatch.test()` loop in the referenced report). No special privileges, keys, or majority control are required; only knowledge (or a few probing attempts) of typical signer hardware timing is needed to land in the disagreement window. Given the codebase's own admission that these budgets are "non-consensus" and hardware-dependent, this divergence is a structural property of the design rather than a rare edge case.

### Recommendation
Do not let signer-side accept/reject decisions for a block proposal hinge on a wall-clock timer whose outcome can vary across nodes. Options:
- Replace or supplement the wall-clock resource budget with the existing deterministic Clarity cost tracker (`ExecutionCost`) as the sole basis for rejecting a proposed block's transactions, since cost is consensus-critical and deterministic across nodes.
- If a wall-clock safety valve must remain (defense-in-depth against runaway bugs), do not treat a single node's timeout as authoritative for rejecting the whole block; instead widen the margin substantially relative to expected worst-case hardware variance, and/or require the signer to retry validation before casting a reject vote, to reduce (though not eliminate) the chance that borderline transactions produce split verdicts.

### Proof of Concept
1. Attacker submits a contract-call transaction (or has it included by a miner) whose per-node evaluation time is deliberately tuned (via a bounded loop or nested `contract-call?`s) to sit close to the configured `block_proposal_max_tx_execution_time_secs` (default budget built in `TransactionResourceBudgets::from_settings`) [9](#0-8) .
2. A miner includes this transaction in a Nakamoto block and broadcasts a block proposal.
3. Signer A (fast, idle) validates the block: `ResourceLimiter::check_not_exceeded` (`elapsed >= max_duration`) returns `Ok` for the transaction, so the block passes and Signer A signs it [5](#0-4) .
4. Signer B (slower, or busy with other work at that moment) validates the identical block; the same check now returns `MaxDurationExceeded`, which `postblock_proposal.rs` converts into a `Problematic` transaction result and a `BlockValidateRejectReason` for the whole proposal [8](#0-7) .
5. The same block is now both a signed candidate (per Signer A) and a rejected candidate (per Signer B), producing signer disagreement/tip instability for a block that no honest party can guarantee will be uniformly validated across the signer set.

### Citations

**File:** clarity/src/vm/resource_limiter.rs (L79-99)
```rust
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
```

**File:** clarity/src/vm/resource_limiter.rs (L181-199)
```rust
/// Specifies the maximum wallclock time and the maximum heap allocation
/// that can be used by an operation. The two relevant operations are
/// contract analysis and execution, each of which have separate budgets
/// (see `TransactionResourceBudgets`).
///
/// Call [`ResourceBudget::start_tracking`] to receive a [`ResourceLimiter`] that
/// fixes the baseline (current time and memory allocation) and that can be polled
/// to ensure usage stays within limits.
///
/// Memory tracking requires that the [`TrackingAllocator`] has been installed.
///
/// This is NOT related to cost tracking. The latter is consensus-critical and therefore
/// deterministic. The purpose of the [`ResourceBudget`] is defense-in-depth: If
/// a bug in clarity evaluation or analysis causes a long runtime or a huge amount
/// of memory being used, the miner will not include it in a block, and the signer
/// will reject the block as problematic.
///
/// During consensus-critical work, the budget MUST be [`ResourceBudget::unlimited`]
/// to ensure determinism.
```

**File:** stackslib/src/chainstate/stacks/miner.rs (L736-784)
```rust
/// Defines limits on computing resources (heap allocation and wallclock time)
/// during processing of contract deploy and call transaction. These are
/// independent of cost tracking and MUST be [`ResourceBudget::unlimited`]
/// during consensus-critical processing, because that must remain deterministic.
///
/// The budgets are limited during the miner's block construction and the
/// signer node's proposal validation to ensure that a smart contract that
/// triggers excessive memory usage or delays is not included in the chain.
/// This is a defense-in-depth measure -- if these budgets are exceeded, that
/// probably means there's an underlying bug in the VM or analysis engine that
/// should be fixed.
pub struct TransactionResourceBudgets {
    /// The budget that applies during clarity evalution, used both during
    /// contract deploy and contract call transactions.
    execution_budget: ResourceBudget,

    /// The budget that applies during contract analysis, only used during
    /// contract deploy transactions.
    analysis_budget: ResourceBudget,
}

impl TransactionResourceBudgets {
    pub fn new() -> Self {
        Self {
            execution_budget: ResourceBudget::unlimited(),
            analysis_budget: ResourceBudget::unlimited(),
        }
    }

    pub fn unlimited() -> Self {
        Self::new()
    }

    pub fn from_settings(settings: &BlockBuilderSettings) -> Self {
        let memory_limit = if settings.max_assembly_mem_bytes > 0 {
            Some(settings.max_assembly_mem_bytes)
        } else {
            None
        };

        Self {
            execution_budget: ResourceBudget::new()
                .with_max_duration(settings.max_execution_time)
                .with_max_memory_use(memory_limit),
            analysis_budget: ResourceBudget::new()
                .with_max_duration(settings.max_analysis_time)
                .with_max_memory_use(memory_limit),
        }
    }
```

**File:** stackslib/src/config/mod.rs (L3288-3309)
```rust
    pub block_rejection_timeout_steps: HashMap<u32, Duration>,
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
    /// Maximum wall-clock time (in seconds) that the contract-analysis
    /// phase of a single transaction may take during mining before timing out.
    ///
    /// If analysis exceeds this limit, the transaction is classified as problematic.
    /// ---
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

**File:** clarity/src/vm/mod.rs (L550-584)
```rust
/// Check for interpreter-level violations of the resource limits
/// (execution time limit or excessive heap allocations).
fn check_interpreter_resource_usage(
    global_context: &GlobalContext,
) -> Result<(), VmExecutionError> {
    global_context
        .execution_resource_limiter
        .check_not_exceeded()
        .map_err(|err| match err {
            ResourceLimitExceeded::MaxDurationExceeded(s) => {
                RuntimeCheckErrorKind::ExecutionResourceBudgetExceeded(format!(
                    "Evaluation took too much time: {s}"
                ))
                .into()
            }
            ResourceLimitExceeded::MaxAllocationExceeded(s) => {
                RuntimeCheckErrorKind::ExecutionResourceBudgetExceeded(format!(
                    "Evaluation used too much memory: {s}"
                ))
                .into()
            }
        })
}

pub fn eval<'a>(
    exp: &'a SymbolicExpression,
    exec_state: &mut ExecutionState,
    invoke_ctx: &'a InvocationContext,
    context: &'a LocalContext,
) -> Result<ValueRef<'a>, VmExecutionError> {
    use crate::vm::representations::SymbolicExpressionType::{
        Atom, AtomValue, Field, List, LiteralValue, TraitReference,
    };

    check_interpreter_resource_usage(exec_state.global_context)?;
```

**File:** stackslib/src/net/api/tests/postblock_proposal.rs (L709-734)
```rust
/// Test that when a transaction's execution phase exceeds the dedicated per-tx
/// execution budget (`block_proposal_max_tx_execution_time_secs`) during
/// block-proposal validation, the block is rejected as containing a problematic
/// transaction, and the rejection blames the offending txid so the miner can
/// drop it from the next proposal.
#[test]
fn test_block_proposal_validation_execution_time_expired_blames_tx() {
    let test_observer = TestEventObserver::new();
    let mut rpc_test = TestRPC::setup_nakamoto(function_name!(), &test_observer);

    // Force every tx execution to exceed its budget: a 0s deadline is already
    // elapsed at the first per-node check. The overall validation timeout and
    // the per-tx analysis limit are left at their (non-zero) defaults, so the
    // per-tx execution limit is what fires here, not the block-level deadline
    // nor the analysis budget.
    rpc_test
        .peer_1
        .network
        .connection_opts
        .block_proposal_max_tx_execution_time_secs = 0;
    rpc_test
        .peer_2
        .network
        .connection_opts
        .block_proposal_max_tx_execution_time_secs = 0;

```
