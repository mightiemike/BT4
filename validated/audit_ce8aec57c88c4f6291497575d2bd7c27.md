### Title
Full transaction (including privacy-pool proof data and signature) logged in plaintext at debug level - ([File: crates/apollo_gateway/src/gateway.rs])

### Summary
The gateway's `add_tx` entry point logs the entire incoming `RpcTransaction` via `Debug` formatting, and separately logs the transaction signature again after processing, both at `debug!` level. Any unprivileged transaction sender who reaches the gateway can trigger these log lines simply by submitting a transaction, causing full transaction contents — including the privacy-pool `proof_facts`/proof material carried by V3 invoke transactions and the transaction signature — to be written to the sequencer's log output in plaintext, whenever the `debug` log level is enabled.

### Finding Description
`GenericGateway::add_tx` unconditionally logs the whole transaction object before any privacy-sensitive fields are stripped: [1](#0-0) 

This is the exact analog of CVE-2020-9486 (Apache NiFi Stateless): a full inbound object (there, a flow definition JSON with secrets; here, an `RpcTransaction`) is serialized into a log line via `Debug`/generic formatting, unconditionally, on every request.

The codebase itself explicitly treats transaction contents as privacy-sensitive: the `RpcInvokeTransaction::V3` variant carries `proof_facts` used for the privacy pool, and the gateway even tracks a dedicated metric for such transactions: [2](#0-1) 

Separately, `starknet_transaction_prover`'s request logging middleware documents the threat model directly: "transaction calldata is private user data per the privacy-pool threat model" and OHTTP-layer request IDs must not be joined with content-level logs to avoid deanonymization: [3](#0-2) 

Yet the gateway's `debug!("Processing tx: {:?}", &tx)` and the subsequent `debug!("Processed tx with signature: {:?}...")` calls bypass that model entirely: they dump the full transaction (calldata, signature, and for V3 invoke transactions, `proof_facts`/proof data intended to preserve sender privacy) to whatever log sink is configured, with no redaction.

Additionally, `StarknetError::internal_with_signature_logging` echoes the raw `TransactionSignature` into error logs on multiple internal-error paths reachable from gateway/mempool processing of an attacker-submitted transaction: [4](#0-3) 

### Impact Explanation
If debug-level logging is enabled in production (a common operational configuration, especially during incident triage or in staging environments feeding shared log aggregation), any external submitter can force sensitive per-transaction data — most importantly the privacy-pool `proof_facts`/proof material that the system is specifically designed to keep confidential/unlinkable — into centralized logs. This directly undermines the privacy guarantees the codebase otherwise takes pains to protect (as evidenced by the OHTTP/unlinkability design in `request_log.rs`), and could deanonymize privacy-pool users or leak calldata that was intended to remain confidential. This maps to CWE-532 (Insertion of Sensitive Information into Log File), matching the reported advisory's bug class, with impact scoped to confidentiality (no funds loss or consensus divergence).

### Likelihood Explanation
Trivial to trigger: every transaction submitted through the gateway's `add_tx` path executes these log statements unconditionally; no special privileges, malformed input, or error condition is required. The only precondition is that the deployment's log level includes `debug`, which is plausible for sequencer nodes during normal operation or troubleshooting (the codebase's own `EnvFilter` defaults elsewhere show `debug` used for entire services, e.g. `starknet_transaction_prover=debug` by default). [5](#0-4) 

### Recommendation
- Remove or redact the full-transaction `Debug` log at `crates/apollo_gateway/src/gateway.rs:197` and the signature log at lines 205-209; log only non-sensitive identifiers (e.g., transaction hash, sender address, tx type) as is already done elsewhere (e.g., `apollo_mempool/src/mempool.rs`'s `#[instrument]` field allowlists).
- Ensure `proof_facts`/`Proof`/`TransactionSignature` types either do not implement `Debug`/`Display` directly, or implement redacted versions (similar to the `Sensitive<T>` wrapper already present in `crates/apollo_config/src/secrets.rs`).
- Audit `StarknetError::internal_with_signature_logging` call sites and stop logging raw signatures in error paths.

### Proof of Concept
1. Deploy the sequencer with `debug` log level enabled for the gateway component (a supported, non-default-but-common operational setting).
2. As an unprivileged client, submit any `RpcTransaction` (e.g., an Invoke V3 transaction using the privacy-pool `proof_facts` field) to the gateway's `add_tx` RPC.
3. Observe the sequencer's log output: the full transaction object (via `debug!("Processing tx: {:?}", &tx)`) and its signature (via the subsequent `debug!("Processed tx with signature: {:?}...")`) are written in plaintext, exposing calldata, signature, and privacy-pool proof data to anyone with log access. [6](#0-5)

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L192-212)
```rust
    pub async fn add_tx(
        &self,
        tx: RpcTransaction,
        p2p_message_metadata: Option<BroadcastedMessageMetadata>,
    ) -> GatewayResult<GatewayOutput> {
        debug!("Processing tx: {:?}", &tx);
        let tx_signature = tx.signature().clone();
        let is_p2p = p2p_message_metadata.is_some();

        let start_time = std::time::Instant::now();
        let ret = self.add_tx_inner(tx, p2p_message_metadata).await;
        let elapsed = start_time.elapsed().as_secs_f64();

        debug!(
            "Processed tx with signature: {:?}. duration: {elapsed} sec, ret: {ret:?}, is_p2p: \
             {is_p2p}",
            &tx_signature,
        );

        ret
    }
```

**File:** crates/apollo_gateway/src/gateway.rs (L219-225)
```rust
        let mut metric_counters = GatewayMetricHandle::new(&tx, &p2p_message_metadata);
        metric_counters.count_transaction_received();
        if let RpcTransaction::Invoke(RpcInvokeTransaction::V3(ref inv)) = tx {
            if !inv.proof_facts.is_empty() {
                metric_counters.count_private_transaction_received();
            }
        }
```

**File:** crates/starknet_transaction_prover/src/server/request_log.rs (L12-25)
```rust
//! It deliberately does NOT bind the id to a span covering the downstream
//! dispatch. For OHTTP traffic this layer runs on the *outer* envelope, whose
//! id is visible to the relay (echoed on the ciphertext response). Propagating
//! that id into the logs describing the *decapsulated* contents would create a
//! join key linking the relay's view (who) to the gateway's view (what),
//! defeating OHTTP unlinkability. Content-level correlation requires a
//! separate, envelope-unlinkable id bound below the OHTTP layer.
//!
//! For OHTTP traffic `status` and `path` also describe the outer envelope: the
//! outer status is 200 whenever decapsulation succeeds (RFC 9458), so inner
//! JSON-RPC failures never appear in this line.
//!
//! Body bytes are never inspected — transaction calldata is private user data
//! per the privacy-pool threat model.
```

**File:** crates/apollo_gateway_types/src/deprecated_gateway_error.rs (L81-88)
```rust
    pub fn internal_with_signature_logging(
        log_message: impl Display,
        tx_signature: &TransactionSignature,
        err: impl std::error::Error,
    ) -> Self {
        let log_message = format!("{log_message}: Transaction signature: {tx_signature:?}");
        Self::internal_with_logging(&log_message, err)
    }
```

**File:** crates/starknet_transaction_prover/src/main.rs (L39-42)
```rust
    // TODO(Avi): Revisit the starknet_transaction_prover=debug default once the service stabilizes.
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| {
        EnvFilter::new("warn,starknet_transaction_prover=debug,privacy_prove=info")
    });
```
