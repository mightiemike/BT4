### Title
Unfunded attacker can exhaust the global declare-compilation semaphore to DoS legitimate class declarations - (File: crates/apollo_gateway/src/gateway.rs)

### Summary
The report describes a class of bug where an unprivileged actor can exhaust a shared, capacity-limited resource without paying any fee, blocking legitimate use for an extended period. The sequencer's Gateway has an analogous unpriced, shared-capacity resource: the `declare_compilation_semaphore`, a process-global permit pool bounding concurrent Sierra→CASM compilations triggered by `Declare` transactions [1](#0-0) . Critically, the permit is acquired and released *before* any fee or balance check occurs, so an attacker never needs to pay for, or even be capable of paying for, the transactions that consume it.

### Finding Description
In `GenericGateway::add_tx_inner`, the flow for a `Declare` transaction is:
1. Stateless validation only (`stateless_tx_validator.validate(&tx)`), which checks structural fields, resource-bound positivity/min-price, tx size, and DA modes — it performs no balance or fee-sufficiency check [2](#0-1) .
2. A non-blocking `try_acquire` on the shared `declare_compilation_semaphore` (default capacity 40, configured via `max_concurrent_declare_compilations`) [3](#0-2) [4](#0-3) .
3. The permit is held only across `convert_rpc_tx_to_internal_and_executable_txs`, which performs the CPU/memory-intensive Sierra-to-CASM compilation, and is dropped immediately after [5](#0-4) .
4. Only *after* the permit is released does stateful validation run — this is where account nonce, balance, fee sufficiency, and `__validate__` execution are actually checked [6](#0-5) [7](#0-6) .

Because the semaphore is acquired for the compilation window irrespective of whether the sender ultimately has sufficient balance/fee, an attacker can submit a stream of syntactically-valid `Declare` transactions with distinct, expensive-to-compile Sierra classes from cheap/unfunded accounts. Each submission occupies one of the (default 40) global compilation permits for the compilation duration and is only rejected afterward, at the stateful-validation stage, for insufficient balance/fee — by which point the permit has already served its purpose of denying capacity to legitimate declarers. This mirrors the reported Boba `Teleportation` bug: a shared daily/global capacity limit (`maxTransferAmountPerDay` / here, `max_concurrent_declare_compilations`) can be exhausted by an unprivileged sender at negligible cost because the fee/funds check happens too late in the pipeline to gate resource consumption.

Config confirms this is a fleet-wide bottleneck, not a per-account limit, and the code comment explicitly acknowledges it is meant to bound "the blast radius of a declare flood," implying the shared, unpriced nature of the resource: [8](#0-7) .

### Impact Explanation
While the attack succeeds, legitimate `Declare` transactions across the whole gateway instance are rejected immediately with a `too_many_concurrent_declare_compilations`-style error [9](#0-8) , effectively preventing any new class declarations from being admitted — a network unable to confirm a whole class of new transactions (declares), analogous to the reported teleport-blocking DoS. This does not affect Invoke/DeployAccount transactions, but class declaration is a required precursor for deploying any new contract type, so sustained denial has broad downstream effects (e.g., blocking new account-class rollouts, upgrades, or dApp deployments) for as long as the attacker keeps the semaphore saturated.

### Likelihood Explanation
The attack requires no signature validity (signature is verified later, during stateful `__validate__`), no funded account, and no fee payment — only a structurally valid `RpcTransaction::Declare` with positive resource bounds above the configured minimum gas price, and a distinct Sierra class to defeat compilation caching. Generating many such classes and submitting them via HTTP is cheap and easily automatable, and the semaphore capacity (default 40) is a small, fixed, cluster-wide number, making sustained saturation by a single or few attackers plausible.

### Recommendation
Reorder the pipeline so ability-to-pay (or at minimum, a minimum-balance/nonce sanity check) is verified before the compilation permit is acquired, or introduce a per-account/IP admission control (e.g., a lightweight balance probe or rate limiting keyed by sender address) ahead of the semaphore acquisition in `add_tx_inner` [10](#0-9) . Alternatively, size/weight the semaphore acquisition by requiring proof of funds (e.g., a cheap on-chain balance read) prior to `try_acquire`, so unfunded declares cannot occupy compilation capacity.

### Proof of Concept
Not independently executed; reasoning is based on static code-path analysis of `add_tx_inner` in `crates/apollo_gateway/src/gateway.rs`, `stateless_transaction_validator.rs`, and `stateful_transaction_validator.rs`. A concrete PoC would submit `N = max_concurrent_declare_compilations` (default 40) concurrent `Declare` RPC transactions from unfunded/fake accounts, each with a distinct, compilation-heavy Sierra class, and observe that a subsequent legitimate, funded `Declare` transaction is rejected with the concurrency-limit error until the attacker's flood of compilations complete and release their permits, at which point the attacker resubmits to keep the semaphore saturated.

Note: I was unable to fully verify the exact content of `validate_declare_tx` (declare-specific stateless checks) or the definitive list of fields checked before permit acquisition, since the index did not return that function body. If precise verification of these specific checks is needed, a full-repo Devin session would be required to inspect `crates/apollo_gateway/src/stateless_transaction_validator.rs` in its entirety.

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L136-138)
```rust
    // Bounds the number of concurrent Sierra-to-CASM compilations triggered by declare
    // transactions. Shared across all clones of the gateway so the limit is process-global.
    declare_compilation_semaphore: Arc<Semaphore>,
```

**File:** crates/apollo_gateway/src/gateway.rs (L235-236)
```rust
        // Perform stateless validations.
        self.stateless_tx_validator.validate(&tx)?;
```

**File:** crates/apollo_gateway/src/gateway.rs (L240-255)
```rust
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

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L158-179)
```rust
    async fn extract_state_nonce_and_run_validations(
        &mut self,
        executable_tx: &ExecutableTransaction,
        mempool_client: SharedMempoolClient,
    ) -> StatefulTransactionValidatorResult<Nonce> {
        let account_nonce =
            self.get_nonce_from_state(executable_tx.contract_address()).await.map_err(|e| {
                // TODO(noamsp): Fix this. Need to map the errors better.
                StarknetError::internal_with_signature_logging(
                    format!(
                        "Failed to get nonce for sender address {}",
                        executable_tx.contract_address()
                    ),
                    &executable_tx.signature(),
                    e,
                )
            })?;
        let skip_validate =
            self.run_pre_validation_checks(executable_tx, account_nonce, mempool_client).await?;
        self.run_validate_entry_point(executable_tx, skip_validate).await?;
        Ok(account_nonce)
    }
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L399-410)
```rust
    async fn run_pre_validation_checks(
        &self,
        executable_tx: &ExecutableTransaction,
        account_nonce: Nonce,
        mempool_client: SharedMempoolClient,
    ) -> StatefulTransactionValidatorResult<bool> {
        self.validate_state_preconditions(executable_tx, account_nonce).await?;
        validate_by_mempool(executable_tx, account_nonce, mempool_client.clone()).await?;
        let skip_validate =
            skip_stateful_validations(executable_tx, account_nonce, mempool_client.clone()).await?;
        Ok(skip_validate)
    }
```
