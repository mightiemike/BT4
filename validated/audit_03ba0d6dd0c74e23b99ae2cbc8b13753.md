## Title
Unauthenticated CPU/memory griefing via Sierra-to-CASM compilation before signature/balance validation in Gateway `add_tx` - (File: `crates/apollo_gateway/src/gateway.rs`)

### Summary
The Sherlock report describes an off-chain matching engine that pays gas to submit trades on behalf of users; an attacker can submit numerous cheap, self-matching orders that force the engine to pay disproportionate gas for negligible fees, griefing the engine's funds. The reachable analog in this sequencer is the Gateway's `add_tx_inner` flow, which triggers CPU/memory-intensive Sierra→CASM compilation for `Declare` transactions **before** the sender's signature or account balance are verified, so an unprivileged transaction sender can force the sequencer to burn compute resources for a fee it will never collect.

### Finding Description
In `GenericGateway::add_tx_inner`, the processing order for a `Declare` transaction is:
1. `check_declare_permissions` (allow-list check only).
2. `stateless_tx_validator.validate(&tx)` — format/size/signature-*format* checks only (no cryptographic signature verification, no balance check).
3. Acquire a `declare_compilation_semaphore` permit and call `convert_rpc_tx_to_internal_and_executable_txs`, which performs the actual Sierra-to-CASM compilation.
4. Only afterwards does `stateful_tx_validator_factory` run stateful validation, which executes `__validate__` (real signature check) and checks account balance/fee sufficiency. [1](#0-0) 

This ordering is explicitly acknowledged in the config comment: compilation is "CPU- and memory-intensive, and it happens during transaction ingestion before the transaction's signature and balance are verified." [2](#0-1) 

The compiler itself performs full Sierra parsing, version extraction, and Sierra-to-CASM compilation plus CASM hashing: [3](#0-2) 

The only mitigation present is a concurrency limiter (`max_concurrent_declare_compilations`, default 40), which caps *simultaneous* compilations but does not rate-limit or economically penalize *sequential* submissions over time: [4](#0-3) [5](#0-4) 

Because the account signature is not cryptographically verified and the balance is not checked until *after* compilation, an attacker can:
- Submit a `Declare` transaction referencing a large (but within stateless size limits) Sierra contract class, from a throwaway/unfunded account address, with an arbitrary (invalid) signature that merely satisfies the stateless format check.
- The Gateway compiles the full class (paying real CPU/memory cost) before discovering — at the stateful-validation stage — that the signature is invalid or the account has insufficient balance to pay any fee.
- The transaction is then rejected and never reaches a block, so **no fee is ever charged** for the compilation work performed.
- The attacker repeats this with new addresses/classes (bounded only by the concurrency semaphore, not by economic cost), continuously consuming the compiler component's CPU/memory for near-zero cost — the same "high-frequency, low-cost-to-attacker, high-cost-to-infrastructure" pattern as the referenced JOJO gas-griefing report, but applied to the sequencer's compilation resources instead of an off-chain engine's on-chain gas spend.

### Impact Explanation
Sustained submission of such crafted `Declare` transactions can exhaust the CPU/memory capacity of the Sierra compiler fleet backing the Gateway (`apollo_compile_to_casm`), degrading or denying transaction ingestion throughput for legitimate users — a network-availability impact ("a network unable to confirm new transactions" in the acceptance criteria) achieved purely through the actions of an unprivileged transaction sender, at negligible/zero verified cost to the attacker since rejected transactions pay no fee.

### Likelihood Explanation
Reaching this path requires only crafting a `RpcTransaction::Declare` with a large, valid-looking (but not necessarily executable/fundable) Sierra class and passing the stateless size/format checks — no privileged role, valid signature, or account funding is required, since those checks occur only after compilation. The concurrency semaphore bounds parallelism but not the aggregate rate of sequential, distinct submissions, so this is straightforwardly automatable by any external actor with network access to the Gateway/HTTP server.

### Recommendation
Move cheap, verifiable economic/authentication checks (e.g., signature verification and balance/fee-sufficiency checks against the declared resource bounds) ahead of the Sierra-to-CASM compilation step, or require a compilation-cost deposit/stake that is forfeited on failed validation. Alternatively, apply a per-sender (or per-IP) rate limit / increasing cost-of-submission specifically for uncompiled `Declare` transactions prior to compilation, independent of the existing concurrency semaphore.

### Proof of Concept
1. Generate N distinct throwaway account addresses (no funding required) and N distinct large-but-within-limits Sierra contract classes.
2. For each, submit a `Declare` v3 transaction via the Gateway's `add_tx` with an arbitrary/invalid signature satisfying only the stateless format checks in `stateless_tx_validator.validate`.
3. Observe (e.g., via the `COMPILATION_DURATION` metric in `apollo_compile_to_casm/src/metrics.rs`) that each transaction triggers a full compilation before being rejected at the stateful-validation stage (`ValidateFailure`/`InsufficientAccountBalance`), with zero fee ever charged.
4. Repeat sequentially at a rate below the `max_concurrent_declare_compilations` limit to sustain continuous, cost-free compiler load.

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L228-266)
```rust
        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }

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

**File:** crates/apollo_gateway_config/src/config.rs (L52-56)
```rust
    /// Maximum number of Sierra-to-CASM compilations (triggered by declare transactions) allowed
    /// to run concurrently. Declares that arrive while this limit is reached are rejected
    /// immediately rather than queued.
    #[validate(range(min = 1))]
    pub max_concurrent_declare_compilations: usize,
```

**File:** crates/apollo_compile_to_casm/src/lib.rs (L52-74)
```rust
impl SierraCompiler {
    pub fn new(compiler: SierraToCasmCompiler) -> Self {
        Self { compiler }
    }

    // TODO(Elin): move (de)serialization to infra. layer.
    #[instrument(skip(self, class), err)]
    #[sequencer_latency_histogram(COMPILATION_DURATION, true)]
    pub fn compile(&self, class: RawClass) -> SierraCompilerResult<RawExecutableHashedClass> {
        let class = SierraContractClass::try_from(class)?;
        let sierra_version =
            class.get_sierra_version().map_err(SierraCompilerError::SierraVersionFormat)?;
        let class = into_contract_class_for_compilation(&class);

        // TODO(Elin): handle resources (whether here or an infra. layer load-balancing).
        let executable_class = self.compiler.compile(class)?;
        // TODO(Elin): consider spawning a worker for hash calculation.
        let executable_class_hash = executable_class.hash(&HashVersion::V2);
        let executable_class = ContractClass::V1((executable_class, sierra_version));
        let executable_class = RawExecutableClass::try_from(executable_class)?;

        Ok((executable_class, executable_class_hash))
    }
```
