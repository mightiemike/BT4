### Title
Client-side-proving (privacy-pool) transaction contents are logged in cleartext, defeating the sequencer's own unlinkability guarantees - ([File: crates/apollo_gateway/src/gateway.rs])

### Summary
The sequencer implements a dedicated privacy-pool / client-side-proving feature (`proof_facts`, `proof` fields on `RpcInvokeTransaction::V3`) and an OHTTP envelope layer whose explicit purpose is to prevent linking a transaction's content to its submitter. Despite this design intent, the transaction-processing path in the gateway, HTTP server, and mempool logs the entire transaction body — including `calldata`, `signature`, `sender_address`, and `proof_facts` — in cleartext at `debug`/`trace` level. Anyone with access to node logs can recover the full content of "private" transactions that were specifically routed through OHTTP/privacy-pool to avoid such correlation, mirroring the Rancher CWE-532 pattern of sensitive request/response data leaking into an operational log stream.

### Finding Description
The codebase contains a privacy-pool feature: `RpcInvokeTransaction::V3` carries `proof_facts`/`proof` fields verified via a dedicated zk circuit (`crates/starknet_proof_verifier/src/proof_verifier.rs`), and a `tower_ohttp` layer exists specifically to give these submissions network-level unlinkability. The gateway's own request-logging middleware documents this threat model explicitly: [1](#0-0) 

stating that request bodies must never be inspected because "transaction calldata is private user data per the privacy-pool threat model."

However, this discipline is not applied in the actual sequencer transaction-submission path. `GenericGateway::add_tx` logs the complete incoming `RpcTransaction` (which includes calldata, signature, sender address, and `proof_facts`) at `debug` level, and again logs the signature after processing: [2](#0-1) 

The HTTP server entry point does the same before any privacy-aware handling occurs: [3](#0-2) 

And the mempool logs the full internal transaction with `trace!("{tx:#?}")`: [4](#0-3) 

Meanwhile, the codebase already has a purpose-built `Sensitive<T>` wrapper elsewhere (config secrets) specifically to prevent this class of leakage via `Debug`/`Display`/`Serialize`: [5](#0-4) 

but this pattern is not applied to transaction fields that the project's own privacy-pool design treats as sensitive (`proof_facts`, calldata for private submissions, etc.). Any operator, log-shipping pipeline, log-storage backend, or third party with read access to sequencer logs (a far larger population than the OHTTP relay operator that the unlinkability design assumes has zero visibility into content) recovers the plaintext transaction — completely undermining the unlinkability the OHTTP/proof-facts machinery was built to provide.

### Impact Explanation
This directly parallels the Rancher CVE-2024-58269 pattern: a component whose entire purpose is to keep data confidential (audit-log redaction in Rancher; OHTTP unlinkability/privacy-pool in the sequencer) is bypassed because a separate, ordinary logging code path echoes the sensitive payload in cleartext. Here, a single unprivileged transaction sender using the privacy-pool/client-side-proving feature has their full transaction content (calldata, signature, sender address, and privacy proof facts) exposed to anyone with log access, defeating the confidentiality/unlinkability guarantee that is the entire value proposition of the feature. This is a concrete, unauthorized information disclosure of user transaction data that the system was specifically engineered to protect, meeting the "unauthorized account action"/privacy-guarantee-violation bar even though it does not directly cause fund loss or consensus divergence.

### Likelihood Explanation
Any external user can trigger this by submitting a transaction (privacy-pool or not) via the standard `add_transaction` / gateway RPC path — no special privileges are required, and it is on the mainline transaction-submission code path (`add_tx` in gateway, HTTP server, and mempool) rather than an edge case. The only precondition is that debug/trace logging is enabled (common for troubleshooting, staging deployments, or log-level misconfiguration), which is a low bar and does not require any node compromise — only third-party or infrastructure access to log storage, exactly as the Rancher advisory requires access to audit-log storage.

### Recommendation
- Apply the existing `Sensitive<T>` redaction pattern (or an equivalent field-level redactor) to `RpcTransaction`'s `Debug`/`Display` implementation, or strip `calldata`, `signature`, `proof_facts`, and `proof` before logging.
- Replace `debug!("Processing tx: {:?}", &tx)` in `crates/apollo_gateway/src/gateway.rs`, `debug!("Received transaction: {tx:?}")` in `crates/apollo_http_server/src/http_server.rs`, and `trace!("{tx:#?}")` in `crates/apollo_mempool/src/mempool.rs` with structured logs that only include non-sensitive identifiers (tx hash, tx type, nonce), consistent with the pattern already used in `Mempool::add_tx`'s `#[instrument]` field list.
- Add an explicit carve-out (or reject logging entirely) for transactions carrying non-empty `proof_facts`/`proof`, consistent with the documented privacy-pool threat model in `request_log.rs`.

### Proof of Concept
1. Enable `debug` (or `trace`) logging on a sequencer node (a supported, documented operational mode via `modify_log_level`/`configure_tracing`).
2. Submit a client-side-proving Invoke V3 transaction (e.g., the sample in `crates/apollo_http_server/resources/deprecated_gateway/invoke_tx_client_side_proving.json`) via `add_transaction`/`add_rpc_tx`.
3. Observe the node's stdout/log stream: the `debug!("Processing tx: {:?}", &tx)` and `debug!("Received transaction: {tx:?}")` lines emit the complete transaction, including `calldata`, `sender_address`, `signature`, and `proof_facts`, in cleartext — the exact data the OHTTP/privacy-pool path was built to keep unlinkable/private, now visible to anyone with log access.

### Citations

**File:** crates/starknet_transaction_prover/src/server/request_log.rs (L24-25)
```rust
//! Body bytes are never inspected — transaction calldata is private user data
//! per the privacy-pool threat model.
```

**File:** crates/apollo_gateway/src/gateway.rs (L191-212)
```rust
    #[sequencer_latency_histogram(GATEWAY_ADD_TX_LATENCY, true)]
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

**File:** crates/apollo_http_server/src/http_server.rs (L296-302)
```rust
async fn add_tx_inner(
    app_state: AppState,
    headers: HeaderMap,
    tx: RpcTransaction,
) -> HttpServerResult<Json<GatewayOutput>> {
    debug!("Received transaction: {tx:?}");
    let gateway_input: GatewayInput = GatewayInput { rpc_tx: tx, message_metadata: None };
```

**File:** crates/apollo_mempool/src/mempool.rs (L585-589)
```rust
    fn add_tx_inner(&mut self, args: AddTransactionArgs) {
        let AddTransactionArgs { tx, account_state } = args;
        info!("Adding transaction to mempool.");
        trace!("{tx:#?}");

```

**File:** crates/apollo_config/src/secrets.rs (L1-8)
```rust
//! A wrapper for values that are considered **sensitive** (e.g. secrets, tokens, URLs).
//!
//! `Sensitive<T>` keeps the inner value available while preventing accidental leakage through
//!  formatting, logging, and serialization:
//!
//! - Display/Debug/Serialize: returns a redacted default value, or a custom redaction via the
//!   provided `redactor`.
//! - Deserialize: transparent, and deserializes exactly like `T`, ignoring the `redactor` field.
```
