Based on my research, I found a concrete analog in this codebase: the gateway's declare-compilation concurrency semaphore is a fixed, shared, per-process quota that any unprivileged declarer can exhaust for free before any fee or balance validation occurs — directly paralleling the "cheap-cost exhaustion of a shared per-block/per-process resource" bug class in the external report.

### Title
Unauthenticated exhaustion of the gateway's `declare_compilation_semaphore` blocks all legitimate Declare transactions - ([File: crates/apollo_gateway/src/gateway.rs])

### Summary
The gateway bounds concurrent Sierra-to-CASM compilations with a small, fixed, process-wide `Semaphore` (`max_concurrent_declare_compilations`, default 40) [1](#0-0) . The permit is acquired right after cheap stateless validation and held only across the (potentially CPU/memory-heavy) Sierra→CASM conversion, before any nonce/balance/fee (stateful) validation occurs [2](#0-1) . An attacker can submit many concurrent Declare transactions (unique dummy Sierra classes, no funds required to pass stateless checks) to hold all 40 permits simultaneously, causing every other Declare submitted meanwhile — including legitimate ones — to be rejected instantly with `TooManyConcurrentDeclareCompilations` [3](#0-2) .

### Finding Description
In `GenericGateway::add_tx_inner`, the flow is: (1) cheap stateless validation (`stateless_tx_validator.validate`), (2) for Declare txs, `try_acquire` on `declare_compilation_semaphore`, (3) the (possibly expensive) Sierra-to-CASM conversion/compilation, (4) release the permit, and only *afterwards* (5) stateful validation which checks nonce, resource bounds pricing and account balance/fee sufficiency via `extract_state_nonce_and_run_validations` [2](#0-1) . Stateless validation of a Declare tx only checks structural properties — Sierra version, bytecode/class-object size limits, sorted/unique entry points, resource-bound non-zero/min-price checks — none of which require the sender to actually hold funds or have a legitimate account state [4](#0-3) .

Because the semaphore is a single, process-global `Arc<Semaphore>` shared across all gateway request handlers [5](#0-4) , and permits are only released after compilation completes (not gated by any fee, stake, or per-sender rate limit), a small number of concurrently in-flight Declare submissions from any unprivileged sender is enough to occupy the entire quota. `try_acquire` fails immediately (no queueing) once permits are exhausted, so honest declarers submitted during the attack window are rejected with `TransactionLimitExceeded` regardless of their fee/tip, unlike other admission-control mechanisms in the codebase (mempool, bouncer) which are fee/priority-ordered and do not create hard, unconditional rejections for legitimate high-fee submitters.

### Impact Explanation
This targets the gateway's transaction-ingestion path reachable by any unprivileged transaction sender, matching "gateway validation" and "mempool admission" concerns in scope. The impact is a network-wide denial of Declare transaction admission: while permits are held, all concurrently-submitted legitimate Declare transactions — regardless of fee paid — are rejected outright rather than queued, which can prevent new contract classes (and by extension new accounts/dApps that depend on those classes) from being confirmed. Sustained concurrent attacker submissions can keep the queue continuously saturated, since the cost of triggering a compilation attempt is just gas-free network bandwidth (no successful, fee-charged transaction is required to occupy a permit).

### Likelihood Explanation
Exploitation only requires submitting valid-looking-but-worthless Declare transactions (unique Sierra programs to avoid class-hash dedup, no requirement to pass balance checks since those occur after compilation) concurrently at a volume matching or exceeding `max_concurrent_declare_compilations` (default 40). No special privileges, staking, or on-chain state are needed, and the existing regression test explicitly demonstrates that a single held permit causes a second declare to be rejected immediately [6](#0-5) .

### Recommendation
- Move the balance/fee-sufficiency and nonce checks (or at least a lightweight balance pre-check) ahead of permit acquisition/compilation, so unfunded senders cannot occupy compilation slots.
- Charge or pre-authorize resources (e.g., require a minimum reserved balance check) before compiling, similar to a "declare fee floor" gate.
- Consider per-sender/per-IP rate limiting on concurrent Declare submissions in addition to the global semaphore, so a single attacker cannot monopolize the entire quota.
- Consider making the semaphore acquisition queue (with a timeout) rather than fail-fast, combined with fairness/anti-starvation logic, so legitimate high-fee declares aren't unconditionally dropped during bursts.

### Proof of Concept
This mirrors the existing test `test_declare_compilation_concurrency_limit` [6](#0-5) : set `max_concurrent_declare_compilations = N`; concurrently submit `N` Declare transactions with distinct dummy Sierra programs (no funded accounts needed since stateless validation only checks structural class properties, per `validate_declare_tx` [4](#0-3) ) and keep their conversions in-flight; a subsequent `N+1`-th legitimate Declare submitted during that window is rejected with `too_many_concurrent_declare_compilations` regardless of its fee/tip [3](#0-2) .

### Citations

**File:** crates/apollo_gateway_config/src/config.rs (L27-38)
```rust
// Compiling a declared Sierra class to CASM is CPU- and memory-intensive, and it happens during
// transaction ingestion before the transaction's signature and balance are verified. Bound the
// number of compilations running concurrently to protect the node from resource exhaustion.
//
// Derivation: compilations are served by the sierracompiler instances, so the safe per-gateway
// bound is the sierracompiler fleet's headroom divided across the gateway fleet, i.e.
// `per_instance_capacity * num_sierracompiler_instances / num_gateway_instances`. Observed
// sierracompiler usage per compilation is small (memory spike ~0.75% of an instance), so a single
// instance can absorb many concurrent compilations. 40 stays well within that envelope while still
// capping the blast radius of a declare flood; retune via the formula above if the
// sierracompiler/gateway instance ratio or per-instance capacity changes.
const DEFAULT_MAX_CONCURRENT_DECLARE_COMPILATIONS: usize = 40;
```

**File:** crates/apollo_gateway/src/gateway.rs (L124-139)
```rust
#[derive(Clone)]
pub struct GenericGateway<
    TStatelessValidator: StatelessTransactionValidatorTrait,
    TTransactionConverter: TransactionConverterTrait,
    TStatefulValidatorFactory: StatefulTransactionValidatorFactoryTrait,
> {
    config: Arc<GatewayConfig>,
    stateless_tx_validator: Arc<TStatelessValidator>,
    stateful_tx_validator_factory: Arc<TStatefulValidatorFactory>,
    mempool_client: SharedMempoolClient,
    transaction_converter: Arc<TTransactionConverter>,
    proof_archive_writer: Arc<dyn ProofArchiveWriterTrait>,
    // Bounds the number of concurrent Sierra-to-CASM compilations triggered by declare
    // transactions. Shared across all clones of the gateway so the limit is process-global.
    declare_compilation_semaphore: Arc<Semaphore>,
}
```

**File:** crates/apollo_gateway/src/gateway.rs (L235-266)
```rust
        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;

        let tx_signature = tx.signature().clone();

        // Declare conversions overload the compiler component's CPU and memory. Reject declares if
        // there are too many declares compiling in parallel. The permit is held only across
        // compilation and released before stateful validation.
        let compilation_permit = if matches!(tx, RpcTransaction::Declare(_)) {
            Some(self.declare_compilation_semaphore.try_acquire().map_err(|_| {
                let error = StarknetError::too_many_concurrent_declare_compilations();
                metric_counters.record_add_tx_failure(&error);
                error
            })?)
        } else {
            None
        };

        let (internal_tx, executable_tx, proof_data) =
            self.convert_rpc_tx_to_internal_and_executable_txs(tx, &tx_signature).await?;
        drop(compilation_permit);

        let mut stateful_transaction_validator = self
            .stateful_tx_validator_factory
            .instantiate_validator(self.config.dynamic_config.native_classes_whitelist.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let nonce = stateful_transaction_validator
            .extract_state_nonce_and_run_validations(&executable_tx, self.mempool_client.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;
```

**File:** crates/apollo_gateway_types/src/deprecated_gateway_error.rs (L94-105)
```rust
    /// Returned when the gateway is already running its maximum number of concurrent declare
    /// compilations and rejects an additional declare rather than queueing it.
    pub fn too_many_concurrent_declare_compilations() -> Self {
        Self {
            code: StarknetErrorCode::KnownErrorCode(
                KnownStarknetErrorCode::TransactionLimitExceeded,
            ),
            message: "Too many declare transactions are being compiled concurrently. Please retry \
                      later."
                .to_string(),
        }
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L280-291)
```rust
    fn validate_declare_tx(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let contract_class = match declare_tx {
            RpcDeclareTransaction::V3(tx) => &tx.contract_class,
        };
        self.validate_sierra_version(&contract_class.sierra_program)?;
        self.validate_class_length(contract_class)?;
        self.validate_entry_points_sorted_and_unique(contract_class)?;
        Ok(())
    }
```

**File:** crates/apollo_gateway/src/gateway_test.rs (L711-759)
```rust
#[rstest]
#[tokio::test(flavor = "multi_thread")]
async fn test_declare_compilation_concurrency_limit(mut mock_dependencies: MockDependencies) {
    mock_dependencies.config.static_config.max_concurrent_declare_compilations = 1;

    // Both declares run stateless validation, so replace the fixture's single-call mock with one
    // that accepts repeated calls.
    let mut mock_stateless_transaction_validator = MockStatelessTransactionValidatorTrait::new();
    mock_stateless_transaction_validator.expect_validate().returning(|_| Ok(()));
    mock_dependencies.mock_stateless_transaction_validator = mock_stateless_transaction_validator;

    // The first declare's conversion performs the Sierra-to-CASM compilation while holding the
    // single permit. Make that conversion block so the second declare arrives while the permit is
    // still held: `compilation_started_sender` signals that the permit is held, and the conversion
    // then parks on `release_compilation_receiver` until the test lets it finish.
    let (compilation_started_sender, compilation_started_receiver) =
        tokio::sync::oneshot::channel();
    let (release_compilation_sender, release_compilation_receiver) = std::sync::mpsc::channel();
    mock_dependencies
        .mock_transaction_converter
        .expect_convert_rpc_tx_to_internal_rpc_tx()
        .return_once(move |_| {
            compilation_started_sender.send(()).unwrap();
            release_compilation_receiver.recv().unwrap();
            // Fail the conversion so the first declare short-circuits here instead of running the
            // full admission path; its outcome is irrelevant, so the specific error is arbitrary.
            Err(TransactionConverterError::ClassNotFound { class_hash: ClassHash::default() })
        });

    let gateway = Arc::new(mock_dependencies.gateway());

    // Spawn the first declare and wait until it holds the permit inside compilation.
    let first_declare_task = {
        let gateway = gateway.clone();
        tokio::spawn(async move { gateway.add_tx(declare_tx(), None).await })
    };
    compilation_started_receiver.await.unwrap();

    // The second declare cannot acquire a permit and must be rejected immediately.
    let second_declare_error = gateway.add_tx(declare_tx(), None).await.unwrap_err();
    assert_eq!(
        second_declare_error.code,
        StarknetErrorCode::KnownErrorCode(KnownStarknetErrorCode::TransactionLimitExceeded)
    );

    // Release the first declare so its task and the blocked worker thread can wind down.
    release_compilation_sender.send(()).unwrap();
    let _ = first_declare_task.await;
}
```
