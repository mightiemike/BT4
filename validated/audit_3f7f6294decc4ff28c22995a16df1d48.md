## Title
Unauthenticated `citrea_haltCommitments` / `citrea_resumeCommitments` RPC methods let any caller mutate sequencer state - (File: `crates/sequencer/src/rpc.rs`)

## Summary
The external report describes a missing `onlyOwner` check on `extendTime`, letting any caller invoke a privileged state-mutating function. The same bug class exists in the sequencer's JSON-RPC surface: `citrea_haltCommitments` and `citrea_resumeCommitments` mutate sequencer state without any access control, while the codebase already has a purpose-built `Auth` mechanism that is supposed to gate exactly this kind of privileged call but was not applied to these two methods.

## Finding Description
The sequencer exposes `citrea_haltCommitments` and `citrea_resumeCommitments` as RPC methods that directly forward a boolean to the commitment service via `SequencerRpcMessage::HaltCommitments` / `ResumeCommitments`, with no caller check at all: [1](#0-0) 

Compare this with `publish_test_block`, which at least gates itself behind `test_mode`: [2](#0-1) 

The node has a dedicated `Auth` RPC middleware whose explicit purpose is to protect privileged methods behind an API key, but the protected set is a hardcoded allow-list that does **not** include `citrea_haltCommitments` / `citrea_resumeCommitments`: [3](#0-2) [4](#0-3) 

The runner unconditionally honors these messages regardless of who sent them: [5](#0-4) 

So, exactly like the audited contract where `extendTime` lacked `onlyOwner` while sibling admin functions had it, here `citrea_haltCommitments`/`citrea_resumeCommitments` lack the `Auth`-gate that the analogous `backup_*` methods have, despite mutating sequencer-critical control flow (whether L2 block commitments are posted to L1 at all).

## Impact Explanation
Any client able to reach the sequencer's JSON-RPC endpoint (no signing key, no API key, no `Auth` check) can call `citrea_haltCommitments` to halt the posting of sequencer commitments to L1 indefinitely, or call `citrea_resumeCommitments` to toggle it back. This is an unauthenticated RPC call that mutates node/rollup-critical state without going through `Auth`, matching the explicitly accepted High-impact class: "an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`." Halting commitments stalls the entire rollup's finality pipeline (no sequencer commitments reach L1, so batch/light-client provers have nothing new to process) until an operator notices and calls resume, or until a malicious actor keeps flipping it.

## Likelihood Explanation
No privileged role, key, or prior access is required — this is reachable by any unprivileged caller with network access to the sequencer's RPC port, which is the same threat model as the "any user could extend the offering purchase time" report (unauthorized caller invoking an admin-only mutator). The only precondition is that the RPC port is reachable, which is the deployment's documented configuration for `eth_sendRawTransaction` and other public methods served from the same `RpcModule`.

## Recommendation
Add `citrea_haltCommitments` and `citrea_resumeCommitments` (and any other sequencer-control RPC methods with similar impact) to the `Auth` middleware's `PROTECTED_METHODS` list in `crates/common/src/rpc/auth.rs`, or otherwise require the sequencer's API key / an explicit authorization check before forwarding the halt/resume signal in `crates/sequencer/src/rpc.rs`.

## Proof of Concept
1. Start a sequencer node with its RPC endpoint reachable (default configuration, no API key set — `api_key: Option<String>` defaults to `None`).
2. From any unauthenticated client, send:
```
curl -X POST http://<sequencer-host>:<port> \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"citrea_haltCommitments","params":[],"id":1}'
```
3. The sequencer stops posting sequencer commitments to L1, as confirmed by the log line `"Sequencer: Halted commitments via RPC"` emitted in [6](#0-5) , with no authentication ever performed since the method is absent from `PROTECTED_METHODS` in `crates/common/src/rpc/auth.rs`.

### Citations

**File:** crates/sequencer/src/rpc.rs (L390-405)
```rust
    /// Sends a sequencer test block signal
    ///
    /// This is mostly used for testing purposes with a mock DA layer.
    async fn publish_test_block(&self) -> RpcResult<()> {
        if !self.context.test_mode {
            return Err(ErrorObject::from(ErrorCode::MethodNotFound).to_owned());
        }

        debug!("Sequencer: citrea_testPublishBlock");
        self.context
            .rpc_message_tx
            .send(SequencerRpcMessage::ProduceTestBlock)
            .map_err(|e| {
                internal_rpc_error(format!("Could not send L2 force block transaction: {e}"))
            })
    }
```

**File:** crates/sequencer/src/rpc.rs (L407-424)
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
```

**File:** crates/common/src/rpc/auth.rs (L11-23)
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
```

**File:** crates/common/src/rpc/auth.rs (L31-47)
```rust
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
