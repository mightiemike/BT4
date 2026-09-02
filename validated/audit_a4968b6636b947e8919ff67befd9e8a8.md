### Title
Unauthenticated `citrea_haltCommitments` / `citrea_resumeCommitments` JSON-RPC methods allow anyone with node RPC access to halt sequencer commitment submission - (File: crates/sequencer/src/rpc.rs)

### Summary
The sequencer exposes `citrea_haltCommitments` and `citrea_resumeCommitments` RPC methods that directly toggle whether the sequencer's commitment service submits sequencer commitments to Bitcoin. Unlike the backup RPC methods, these are not listed in `PROTECTED_METHODS` and thus are not gated by the `Auth` middleware's API-key check, meaning any caller who can reach the sequencer's JSON-RPC endpoint can mutate this critical piece of node state without authentication.

### Finding Description
`Auth::call` only requires an API key for methods in `PROTECTED_METHODS = ["backup_create", "backup_validate", "backup_info"]`; every other method, including `citrea_haltCommitments` and `citrea_resumeCommitments`, bypasses the API-key check entirely and is dispatched straight to the underlying service. [1](#0-0) 

The sequencer RPC trait defines these two methods with no additional authorization inside the handler itself: [2](#0-1) 

The handlers simply forward a `SequencerRpcMessage::HaltCommitments` / `ResumeCommitments` message over an unbounded channel to the sequencer runner loop, with no caller-identity check: [2](#0-1) 

The runner loop unconditionally honors these messages and forwards a halt/resume boolean directly to the commitment service: [3](#0-2) 

The RPC server itself applies the `Auth` middleware uniformly to all registered methods based only on the `PROTECTED_METHODS` allow-list, so nothing outside of `auth.rs` provides a secondary guard for these two methods: [4](#0-3) 

This is directly analogous to the reported `collectPositionSwapFee` issue: a function that is clearly meant to be privileged (per the naming/intent — halting/resuming the sequencer's DA commitment submission is an operational control, similar in kind to the already-protected `backup_*` endpoints) has no access-control check applied, allowing any caller.

### Impact Explanation
This matches the High-severity class explicitly defined in scope: "an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`." An unauthenticated attacker with network access to the sequencer's RPC port can call `citrea_haltCommitments` to indefinitely stop the sequencer from publishing sequencer commitments to L1 (or call `citrea_resumeCommitments` to flip it back), directly mutating sequencer node state that governs L2→L1 commitment production, entirely bypassing the `Auth` layer that was clearly designed to protect sensitive administrative RPC surface (as evidenced by its use for the `backup_*` endpoints).

### Likelihood Explanation
Likelihood is high for any deployment where the sequencer's JSON-RPC endpoint is reachable by untrusted parties (e.g., exposed through a load balancer, proxy, or any non-loopback bind), since the call requires no credentials, no special role, and is a single unauthenticated RPC request.

### Recommendation
Add `citrea_haltCommitments` and `citrea_resumeCommitments` to `PROTECTED_METHODS` in `crates/common/src/rpc/auth.rs` (or otherwise require the configured API key / an equivalent authorization check) so that only authorized operators can toggle sequencer commitment submission via RPC.

### Proof of Concept
1. Start a Citrea sequencer node with its RPC endpoint reachable (default config, no special auth configured for non-`backup_*` methods).
2. As an unauthenticated remote client, issue:
```
curl -X POST http://<sequencer-rpc-host>:<port> \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"citrea_haltCommitments","params":[]}'
```
3. Observe (via logs, as in `crates/sequencer/src/runner.rs:1271-1277`) that the sequencer logs `"Sequencer: Halted commitments via RPC"` and the commitment service stops submitting sequencer commitments — achieved without providing any API key, unlike the equivalent call to `backup_create`/`backup_validate`/`backup_info`, which is rejected with HTTP 401 absent a valid key. [5](#0-4)

### Citations

**File:** crates/common/src/rpc/auth.rs (L11-47)
```rust
const PROTECTED_METHODS: [&str; 3] = ["backup_create", "backup_validate", "backup_info"];

#[derive(Debug, Clone)]
pub struct Auth<S> {
    service: S,
    api_key: Option<String>,
}

impl<S> Auth<S> {
    pub fn new(service: S, api_key: Option<String>) -> Self {
        Self { service, api_key }
    }
}

impl<'a, S> RpcServiceT<'a> for Auth<S>
where
    S: RpcServiceT<'a> + Send + Sync + Clone + 'a,
{
    type Future = BoxFuture<'a, MethodResponse>;

    fn call(&self, req: Request<'a>) -> Self::Future {
        let method = req.method_name();
        let service = self.service.clone();
        let api_key = self.api_key.clone().map(Value::from);

        if !PROTECTED_METHODS.contains(&method) {
            return Box::pin(service.call(req));
        }

        let Some(api_key) = api_key else {
            return Box::pin(async move {
                MethodResponse::error(
                    req.id().clone(),
                    ErrorObjectOwned::owned(401, "Cannot access protected method", None::<String>),
                )
            });
        };
```

**File:** crates/sequencer/src/rpc.rs (L407-425)
```rust
    /// Halt sequencer commitments
    async fn halt_commitments(&self) -> RpcResult<()> {
        debug!("Sequencer: citrea_haltCommitments");
        self.context
            .rpc_message_tx
            .send(SequencerRpcMessage::HaltCommitments)
            .map_err(|e| internal_rpc_error(format!("Could not send halt commitments signal: {e}")))
    }

    /// Resume sequencer commitments
    async fn resume_commitments(&self) -> RpcResult<()> {
        debug!("Sequencer: citrea_resumeCommitments");
        self.context
            .rpc_message_tx
            .send(SequencerRpcMessage::ResumeCommitments)
            .map_err(|e| {
                internal_rpc_error(format!("Could not send resume commitments signal: {e}"))
            })
    }
```

**File:** crates/sequencer/src/runner.rs (L1271-1286)
```rust
                        Some(SequencerRpcMessage::HaltCommitments) => {
                            // Forward halt signal to commitment service
                            if let Err(e) = halt_commitment_tx.send(true) {
                                error!("Failed to send halt signal to commitment service: {}", e);
                            } else {
                                info!("Sequencer: Halted commitments via RPC");
                            }
                        },
                        Some(SequencerRpcMessage::ResumeCommitments) => {
                            // Forward resume signal to commitment service
                            if let Err(e) = halt_commitment_tx.send(false) {
                                error!("Failed to send resume signal to commitment service: {}", e);
                            } else {
                                info!("Sequencer: Resumed commitments via RPC");
                            }
                        },
```

**File:** crates/common/src/rpc/server.rs (L42-45)
```rust
    let rpc_middleware = RpcServiceBuilder::new()
        .layer_fn(move |s| super::auth::Auth::new(s, rpc_config.api_key.clone()))
        .layer_fn(super::Logger)
        .layer_fn(RpcMetrics);
```

**File:** bin/citrea/tests/common/client.rs (L936-956)
```rust
    /// Halt sequencer commitments
    pub(crate) async fn sequencer_halt_commitments(
        &self,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let _: () = self
            .http_client
            .request("citrea_haltCommitments", rpc_params![])
            .await?;
        Ok(())
    }

    /// Resume sequencer commitments
    pub(crate) async fn sequencer_resume_commitments(
        &self,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let _: () = self
            .http_client
            .request("citrea_resumeCommitments", rpc_params![])
            .await?;
        Ok(())
    }
```
