### Title
Unauthenticated `citrea_haltCommitments` / `citrea_resumeCommitments` RPC methods allow anyone to mutate sequencer commitment state - (File: crates/sequencer/src/rpc.rs)

### Summary
The sequencer's JSON-RPC methods `citrea_haltCommitments` and `citrea_resumeCommitments` mutate privileged sequencer state (whether L2 state-transition commitments are published to the DA layer) but have no authorization check at all, unlike other privileged methods that are protected. This mirrors the Maple `closeLoan()` bug: a state-mutating entry point that should be restricted to a privileged caller is instead callable by anyone who can reach the RPC endpoint.

### Finding Description
`SequencerRpcServerImpl::halt_commitments` and `::resume_commitments` directly forward `SequencerRpcMessage::HaltCommitments` / `ResumeCommitments` to the sequencer's internal control channel with no caller check whatsoever: [1](#0-0) 

Compare this to `publish_test_block`, which explicitly gates itself with a `test_mode` check before acting: [2](#0-1) 

The node's `Auth` middleware, which is the mechanism used elsewhere in this codebase to protect sensitive RPC methods with an API key, only guards a fixed allowlist of methods (`backup_create`, `backup_validate`, `backup_info`) and passes every other method straight through unauthenticated: [3](#0-2) 

`citrea_haltCommitments` and `citrea_resumeCommitments` are declared as regular public RPC trait methods with no additional check, and are not in `PROTECTED_METHODS`: [4](#0-3) 

The binding that is broken: the sequencer's decision to publish/withhold state-transition commitments to Bitcoin should be an operator-authorized action (`operator/authority == caller`), but the actual enforced condition is `true` (any caller). This is the same class of bug as Maple's `closeLoan()`: a caller-restricted state mutation that has no `require(msg.sender == authorized)` guard.

### Impact Explanation
Any unprivileged party with network access to the sequencer's RPC endpoint can call `citrea_haltCommitments` to stop the sequencer from posting new sequencer commitments to the DA layer, and can call `citrea_resumeCommitments` to toggle it back on at will. This is an unauthenticated JSON-RPC call that mutates node state — the exact High-impact category defined in this scan's rules ("an unauthenticated JSON-RPC call that mutates node state or bypasses `Auth`"). Sustained or repeated halting stalls commitment publication to L1, which delays proof generation/finality for the rollup, without requiring any credentials, sequencer role, or key compromise.

### Likelihood Explanation
Likelihood is high for any deployment where the sequencer RPC port is reachable by non-operator clients (as it must be for `eth_sendRawTransaction`, etc., which are also unauthenticated per the same `Auth` middleware allowlist). No signature, API key, or `test_mode` flag is required — a single unauthenticated RPC call is sufficient.

### Recommendation
Add authorization to `halt_commitments` and `resume_commitments`, either by:
- Adding them to `PROTECTED_METHODS` in `crates/common/src/rpc/auth.rs` so they require the API key, or
- Adding an explicit caller/authority check inside `SequencerRpcServerImpl::halt_commitments` / `::resume_commitments` analogous to the `test_mode` check already used in `publish_test_block`.

### Proof of Concept
1. Start a Citrea sequencer node with default RPC configuration (no API key set, or API key set but these two methods excluded from `PROTECTED_METHODS`).
2. From any unauthenticated client, send:
```json
{"jsonrpc":"2.0","method":"citrea_haltCommitments","params":[],"id":1}
```
3. Observe (per [5](#0-4) ) the call succeeds and `SequencerRpcMessage::HaltCommitments` is delivered to the sequencer's internal task, stopping commitment publication — with no check that the caller is the sequencer operator.

### Citations

**File:** crates/sequencer/src/rpc.rs (L175-181)
```rust
    /// Halt sequencer commitments
    #[method(name = "citrea_haltCommitments")]
    async fn halt_commitments(&self) -> RpcResult<()>;

    /// Resume sequencer commitments
    #[method(name = "citrea_resumeCommitments")]
    async fn resume_commitments(&self) -> RpcResult<()>;
```

**File:** crates/sequencer/src/rpc.rs (L393-405)
```rust
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

**File:** crates/common/src/rpc/auth.rs (L11-38)
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
```
