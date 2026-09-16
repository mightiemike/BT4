### Title
Gateway DEBUG logs leak full transaction contents including client-side proving `proof_facts`/`proof`, contradicting the codebase's own privacy-pool threat model - (File: crates/apollo_gateway/src/gateway.rs)

### Summary
`RpcTransaction` derives a plain `Debug` implementation that serializes every field verbatim (calldata, signature, `proof_facts`, `proof`, etc.), and `GenericGateway::add_tx` logs the entire incoming transaction via `debug!("Processing tx: {:?}", &tx);` before and after processing, at the DEBUG level.

### Finding Description
`GenericGateway::add_tx` in `crates/apollo_gateway/src/gateway.rs:191-212` logs:
```
debug!("Processing tx: {:?}", &tx);
...
debug!("Processed tx with signature: {:?}. duration: {elapsed} sec, ret: {ret:?}, is_p2p: {is_p2p}", &tx_signature);
``` [1](#0-0) 

`RpcTransaction` and its variants (e.g. `RpcInvokeTransaction::V3`) derive `Debug` directly (`#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash, EnumDiscriminants)]`), which prints every field unredacted, including `calldata`, `signature`, `proof_facts`, and `proof`. [2](#0-1) 

Elsewhere in the same codebase (`starknet_transaction_prover/src/server/request_log.rs`), the authors explicitly document that this is considered sensitive data: "*Body bytes are never inspected — transaction calldata is private user data per the privacy-pool threat model.*" and the middleware is deliberately designed to avoid correlating or exposing per-request contents at the HTTP layer. [3](#0-2) 

The gateway itself distinguishes "private" transactions from "public" ones based on whether `proof_facts` is non-empty (`RpcTransaction::Invoke(RpcInvokeTransaction::V3(ref inv))` → `count_private_transaction_received()`), confirming that `proof_facts`/`proof` fields carry privacy-sensitive, client-side-proving data that the system explicitly tracks as a distinct, more sensitive category of transaction. [4](#0-3) 

Despite this internal recognition that calldata/proof data for "private" transactions must not be casually exposed, `add_tx` logs the full transaction Debug representation — including `proof_facts`, `proof`, `calldata`, and `signature` — at DEBUG level with no redaction, directly analogous to the Kafka `NetworkClient` DEBUG-log issue where entire sensitive request/response objects (e.g. `SaslAuthenticateRequest`) were dumped to logs whenever DEBUG logging was enabled.

### Impact Explanation
If DEBUG logging is enabled on a gateway node (a supported, documented operational mode — see `apollo_monitoring_endpoint`'s runtime log-level control endpoint), every transaction submitted by any unprivileged sender — including "private"/client-side-proving invoke transactions using the privacy-pool proof mechanism — has its full contents, including `proof_facts` and `proof`, written to plaintext logs. This defeats the unlinkability/privacy properties the system was explicitly designed to protect (as documented in `request_log.rs`), exposing private user data (proof facts tying a transaction to prior deposits/commitments in the privacy pool) to anyone with log access (operators, log aggregation pipelines, third-party monitoring). This is an information-exposure vulnerability (CWE-532) matching the reported advisory's class, with confidentiality impact on data the codebase itself classifies as sensitive.

### Likelihood Explanation
Any external, unprivileged actor can trigger this simply by submitting a transaction (including a private/client-side-proving invoke transaction) to the gateway — no special privileges are required to cause the sensitive data to be generated and logged. The only precondition is that the operator has DEBUG-level logging enabled for the `apollo_gateway` target, which the codebase explicitly supports enabling/disabling live via `apollo_monitoring_endpoint`'s `SET_LOG_LEVEL` route, making this a realistic operational configuration rather than a hypothetical one.

### Recommendation
- Remove or redact the full transaction Debug dump in `add_tx`; log only non-sensitive, already-public identifiers (e.g., `tx_hash`, transaction type, sender address) instead of `{:?}` on the whole `tx`.
- For "private" transactions (non-empty `proof_facts`/`proof`), suppress or specifically redact those fields in any Debug/logging path, consistent with the "private user data" designation already used elsewhere (`request_log.rs`).
- Consider implementing a custom `Debug` for `RpcTransaction`/`RpcInvokeTransactionV3` (similar to the existing `Sensitive<T>` wrapper in `apollo_config::secrets`) that redacts `proof_facts`, `proof`, `calldata`, and `signature` by default, only exposing them via an explicit "expose" API when strictly necessary.

### Proof of Concept
1. Enable DEBUG logging for the `apollo_gateway` crate (e.g., via the monitoring endpoint's `set_log_level/apollo_gateway/debug`, or via startup log configuration).
2. Submit an `INVOKE` V3 transaction with non-empty `proof_facts`/`proof` (a "private" client-side-proving transaction) to the gateway's `add_tx` RPC.
3. Observe the gateway's log output: `debug!("Processing tx: {:?}", &tx);` prints the full `RpcTransaction` Debug representation, including the `proof_facts` and `proof` fields, in plaintext in the logs — exposing data the system's own documentation (`request_log.rs`) calls "private user data per the privacy-pool threat model."

### Citations

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

**File:** crates/starknet_api/src/rpc_transaction.rs (L55-70)
```rust
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize, Hash, EnumDiscriminants)]
#[strum_discriminants(
    name(RpcTransactionLabelValue),
    derive(IntoStaticStr, EnumIter, VariantNames),
    strum(serialize_all = "snake_case")
)]
#[serde(tag = "type")]
#[serde(deny_unknown_fields)]
pub enum RpcTransaction {
    #[serde(rename = "DECLARE")]
    Declare(RpcDeclareTransaction),
    #[serde(rename = "DEPLOY_ACCOUNT")]
    DeployAccount(RpcDeployAccountTransaction),
    #[serde(rename = "INVOKE")]
    Invoke(RpcInvokeTransaction),
}
```

**File:** crates/starknet_transaction_prover/src/server/request_log.rs (L24-25)
```rust
//! Body bytes are never inspected — transaction calldata is private user data
//! per the privacy-pool threat model.
```
