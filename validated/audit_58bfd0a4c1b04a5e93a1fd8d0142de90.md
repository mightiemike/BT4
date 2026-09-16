### Title
Raw transaction content (calldata, signature, proof_facts) unfilteredly written into tracing logs/spans on every gateway invocation - ([File: crates/apollo_gateway/src/gateway.rs] / [File: crates/apollo_gateway/src/stateless_transaction_validator.rs])

### Summary
The Starknet gateway logs the complete `RpcTransaction` (via `Debug`) on every transaction submission, and separately instruments the stateless validator with `#[instrument]` without skipping the `tx` argument, causing the full transaction payload — including calldata, signature, and (for privacy-pool "client-side proving" invokes) `proof_facts` — to be recorded into tracing output/spans on every call. This is the same bug class as CVE-2026-15737: unfiltered raw user content written into instrumentation output that flows into a durable, broadly-readable log sink.

### Finding Description
`GenericGateway::add_tx` unconditionally logs the entire incoming transaction: [1](#0-0) 
This `debug!("Processing tx: {:?}", &tx)` serializes the whole `RpcTransaction`, which for `RpcInvokeTransaction::V3` includes `calldata`, `signature`, `sender_address`, `nonce`, and `proof_facts`.

Separately, `StatelessTransactionValidator::validate` is annotated with `#[instrument(skip(self), level = Level::INFO)]` but does **not** skip its `tx: &RpcTransaction` parameter: [2](#0-1) 
Because `tracing::instrument` auto-records all non-skipped arguments as span fields via their `Debug` implementation, every call to `validate()` — i.e., every transaction submitted through the gateway — creates an INFO-level span carrying the transaction's full `Debug` representation as a field. This is functionally identical to the AgentCore SDK bug: raw, per-invocation user content written into span/log fields without filtering or redaction, destined for a log-aggregation backend (the sequencer's tracing subscriber output, the analog of CloudWatch's `aws/spans` group).

Notably, the codebase itself explicitly recognizes this exact risk in a sibling component. The transaction-prover HTTP middleware documents that request bodies (transaction calldata) must never be logged because "transaction calldata is private user data per the privacy-pool threat model": [3](#0-2) 
The gateway's own metrics distinguish "private" transactions (non-empty `proof_facts`, i.e., client-side-proven/privacy-pool invokes) from public ones: [4](#0-3) 
Yet the gateway's `add_tx` and `validate` code paths log/instrument the complete transaction object anyway, directly contradicting the privacy design intent enforced elsewhere in the same codebase.

### Impact Explanation
Any principal with read access to the sequencer's log/trace output (operators, log aggregation pipelines, monitoring backends) can recover complete raw calldata, signatures, sender addresses, and `proof_facts` for every transaction submitted by any user, including those explicitly intended to be private (client-side-proving invokes distinguished by non-empty `proof_facts`). This is a confidentiality break of user transaction content that the system's own privacy-pool threat model treats as sensitive, undermining the unlinkability/privacy guarantees the prover's OHTTP path was built to protect.

### Likelihood Explanation
This triggers on every single transaction submission with no special conditions — any unprivileged transaction sender reaches it simply by calling `add_tx`, since the `debug!` log line and the `#[instrument]`-generated span execute unconditionally on the hot path. In any deployment with DEBUG-level logging enabled (common for `debug!`) or with INFO-level tracing exported to a backend (for the `#[instrument]` span on `validate`), the sensitive fields are captured automatically without any deliberate operator action needed.

### Recommendation
- Remove or redact the `debug!("Processing tx: {:?}", &tx)` line in `GenericGateway::add_tx`, logging only non-sensitive identifiers (e.g., transaction hash, transaction type) as already done a few lines later with `tx_hash`.
- Add `tx` to the `skip` list of `#[instrument(skip(self, tx), ...)]` on `StatelessTransactionValidator::validate`, or implement a redacted `Debug`/custom field extractor that surfaces only the transaction type and hash.
- Audit other `#[instrument]` usages across `apollo_gateway`, `apollo_mempool`, and `apollo_batcher` for un-skipped transaction/calldata parameters that would similarly leak into spans.
- Apply the `Sensitive<T>` wrapper pattern already present in the codebase (`crates/apollo_config/src/secrets.rs`) to calldata/proof_facts fields, or scrub them before any `Debug`/`tracing` formatting.

### Proof of Concept
1. Submit an `RpcTransaction::Invoke(RpcInvokeTransaction::V3)` with non-empty `proof_facts` (a client-side-proven/private invoke) to the gateway's `add_tx` endpoint.
2. With `RUST_LOG=debug` (or any INFO-level tracing subscriber attached), observe that `crates/apollo_gateway/src/gateway.rs:197` emits `"Processing tx: {full Debug of RpcTransaction}"`, including calldata, signature, sender address, and `proof_facts`.
3. Independently, observe that the `http_request`/`validate` span emitted from `StatelessTransactionValidator::validate` (INFO level) carries the `tx` argument as a recorded field, since it is not in the `skip(...)` list — reproducible by attaching any span-recording subscriber (e.g., `tracing_subscriber::fmt` with span events, or a test using `tracing_test::traced_test` as already used elsewhere in the repo, e.g. `crates/starknet_transaction_prover/src/server/request_span_test.rs:52-80`) and asserting the logged/recorded span fields contain the raw calldata/proof_facts values.

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L192-198)
```rust
    pub async fn add_tx(
        &self,
        tx: RpcTransaction,
        p2p_message_metadata: Option<BroadcastedMessageMetadata>,
    ) -> GatewayResult<GatewayOutput> {
        debug!("Processing tx: {:?}", &tx);
        let tx_signature = tx.signature().clone();
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L31-34)
```rust
impl StatelessTransactionValidator {
    #[instrument(skip(self), level = Level::INFO)]
    pub fn validate(&self, tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        // TODO(Arni, 1/5/2024): Add a mechanism that validate the sender address is not blocked.
```

**File:** crates/starknet_transaction_prover/src/server/request_log.rs (L24-25)
```rust
//! Body bytes are never inspected — transaction calldata is private user data
//! per the privacy-pool threat model.
```

**File:** crates/apollo_dashboard/resources/dev_grafana.json (L725-727)
```json
          "title": "Transactions Received by Proof Type",
          "description": "The number of private (invoke_v3 with non-empty proof_facts) vs public transactions received (over the selected time range)",
          "type": "stat",
```
