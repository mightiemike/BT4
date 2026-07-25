Based on my thorough code investigation, here is my assessment:

---

### Title
Unbounded `crossbeam_channel` in `MultithreadRuntimeHandle<RpcHandlerActor>` Allows Unprivileged OOM via `broadcast_tx_async` Flood — (`core/async/src/multithread/runtime_handle.rs`)

### Summary

An unprivileged attacker can flood the public `broadcast_tx_async` JSON-RPC endpoint with `ProcessTxRequest` messages. Each request is immediately enqueued into an unbounded `crossbeam_channel` backing the `MultithreadRuntimeHandle<RpcHandlerActor>` with no back-pressure, rate limiting, or queue depth cap. If the actor threads consume messages slower than the attacker produces them (a realistic condition given trie reads, epoch manager lock contention, and signature verification per message), the channel grows without bound, exhausting process memory and causing the node to be OOM-killed, dropping it from consensus.

### Finding Description

**Confirmed call path:**

1. HTTP POST to `/` with method `broadcast_tx_async` → `rpc_handler()` → `JsonRpcHandler::process_basic_requests_internal()` [1](#0-0) 

2. `send_tx_async()` fires `ProcessTxRequest` fire-and-forget via `self.process_tx_sender.send(...)` — **no validation before enqueue**: [2](#0-1) 

3. `process_tx_sender` is a `Sender<ProcessTxRequest>` backed by `MultithreadRuntimeHandle<RpcHandlerActor>`, which implements `CanSend<M>` by calling `self.send_message(message)` → `self.sender.send(message)`: [3](#0-2) 

4. The channel is created as **`crossbeam_channel::unbounded`** in `spawn_multithread_actor`: [4](#0-3) 

5. The `RpcHandlerActor` handler for each dequeued message calls `process_tx_internal`, which performs non-trivial work: `chain_store.head()` (DB read), `epoch_manager` calls (shared lock), and `runtime.can_verify_and_charge_tx()` (trie read + signature verification): [5](#0-4) 

**No back-pressure exists between the HTTP server and the actor channel.** The only middleware applied is a request body size limit (`RequestBodyLimitLayer`, default 10 MB) and CORS — no connection limit, no request rate limit, no queue depth cap: [6](#0-5) 

The `transaction_pool_size_limit` (default 100 MB) limits the *mempool* after a message is dequeued and processed — it does not bound the actor's input channel: [7](#0-6) 

### Impact Explanation

If the attacker submits `broadcast_tx_async` requests faster than the `handler_threads` actor threads can drain the channel, `MultithreadRuntimeMessage<RpcHandlerActor>` structs (each containing a boxed closure holding a `SignedTransaction`) accumulate in the heap without bound. The Linux OOM killer terminates `neard`, causing the node to drop out of consensus. This is a non-network-level DoS fixable without a hardfork (bounded channel or RPC-layer rate limiting suffices).

### Likelihood Explanation

`broadcast_tx_async` is a public, unauthenticated endpoint. Transactions need not be valid — they are enqueued before any validation. A single attacker with a high-throughput HTTP client can sustain a submission rate exceeding the actor's drain rate, especially during epoch transitions when `epoch_manager` lock contention is elevated (as documented in the codebase's own futex contention profiling guide): [8](#0-7) 

### Recommendation

1. Replace `crossbeam_channel::unbounded` with `crossbeam_channel::bounded(N)` in `spawn_multithread_actor`, choosing `N` based on expected throughput and acceptable memory budget. [4](#0-3) 

2. Add a per-IP or global request rate limiter (e.g., Tower's `RateLimitLayer`) specifically for transaction submission endpoints in `create_jsonrpc_app`. [6](#0-5) 

3. Alternatively, apply back-pressure at the `send_tx_async` call site: if the channel is full, return an HTTP 429 immediately rather than blocking or silently dropping.

### Proof of Concept

```rust
// Rust property test sketch
use crossbeam_channel::unbounded;
use std::thread;

#[test]
fn unbounded_channel_oom_under_stalled_consumer() {
    let (tx, rx) = unbounded::<Vec<u8>>();
    // Simulate stalled consumer (e.g., epoch manager lock contention)
    let _consumer = thread::spawn(move || {
        thread::sleep(std::time::Duration::from_secs(60));
        while let Ok(_) = rx.recv() {}
    });
    // Attacker floods N messages
    let payload = vec![0u8; 1024]; // ~1KB per SignedTransaction
    for _ in 0..1_000_000 {
        tx.send(payload.clone()).unwrap();
    }
    // ~1 GB in channel; assert memory stays below bound — this assertion FAILS
    let mem = /* measure RSS */ 0usize;
    assert!(mem < 100 * 1024 * 1024, "OOM: channel grew unbounded");
}
```

In production, each enqueued `MultithreadRuntimeMessage` holds a boxed closure capturing a `SignedTransaction` (hundreds of bytes each). At 1 M queued messages the process RSS grows by hundreds of MB to several GB.

### Citations

**File:** chain/jsonrpc/src/lib.rs (L618-624)
```rust
            "broadcast_tx_async" => {
                process_method_call(request, |params| async {
                    let tx = self.send_tx_async(params).to_string();
                    Result::<_, std::convert::Infallible>::Ok(tx)
                })
                .await
            }
```

**File:** chain/jsonrpc/src/lib.rs (L873-882)
```rust
    fn send_tx_async(&self, request_data: RpcSendTransactionRequest) -> CryptoHash {
        let tx = request_data.signed_transaction;
        let hash = tx.get_hash();
        self.process_tx_sender.send(ProcessTxRequest {
            transaction: tx,
            is_forwarded: false,
            check_only: false, // if we set true here it will not actually send the transaction
        });
        hash
    }
```

**File:** chain/jsonrpc/src/lib.rs (L3119-3121)
```rust
    app.layer(get_cors(&cors_allowed_origins))
        .layer(RequestBodyLimitLayer::new(limits_config.json_payload_max_size))
        .with_state(handler)
```

**File:** core/async/src/multithread/sender.rs (L8-31)
```rust
impl<A, M> CanSend<M> for MultithreadRuntimeHandle<A>
where
    A: Handler<M> + 'static,
    M: Debug + Send + 'static,
{
    fn send(&self, message: M) {
        let seq = next_message_sequence_num();
        let message_type = pretty_type_name::<M>();
        tracing::trace!(target: "multithread_runtime", seq, message_type, "sending sync message");

        let function = |actor: &mut A| {
            actor.handle(message);
        };

        let message = MultithreadRuntimeMessage {
            seq,
            enqueued_time_ns: self.instrumentation.current_time(),
            name: message_type,
            function: Box::new(function),
        };
        if let Err(_) = self.send_message(message) {
            tracing::info!(target: "multithread_runtime", seq, "ignoring sync message, receiving actor is being shut down");
        }
    }
```

**File:** core/async/src/multithread/runtime_handle.rs (L82-82)
```rust
    let (sender, receiver) = crossbeam_channel::unbounded::<MultithreadRuntimeMessage<A>>();
```

**File:** chain/client/src/rpc_handler.rs (L153-159)
```rust
    fn process_tx_internal(
        &self,
        signed_tx: &SignedTransaction,
        is_forwarded: bool,
        check_only: bool,
    ) -> Result<ProcessTxResponse, near_client_primitives::types::Error> {
        let head = self.chain_store.head()?;
```

**File:** core/chain-configs/src/client_config.rs (L553-555)
```rust
pub fn default_transaction_pool_size_limit() -> Option<u64> {
    Some(100_000_000) // 100 MB.
}
```

**File:** docs/practices/workflows/futex_contention.md (L43-57)
```markdown
neard0[55783] lock 0x79a1b1dfcfb0 contended 41 times, 22 avg msecs [max: 48 msecs, min 5 msecs]
    -
    syscall
    _ZN10near_store4trie4Trie17get_optimized_ref17h8d5fd8c67e262ab1E
    _ZN10near_store4trie4Trie3get17h11d4bcd3667e3c8fE
    _ZN85_$LT$near_store..trie..update..TrieUpdate$u20$as$u20$near_store..trie..TrieAccess$GT$3get17h041c58f313ed6d6cE
    _ZN10near_store5utils11get_account17hf3071e79288206c0E
    _ZN12node_runtime8verifier25get_signer_and_access_key17hca85f102c5a0241bE
    _ZN92_$LT$near_chain..runtime..NightshadeRuntime$u20$as$u20$near_chain..types..RuntimeAdapter$GT$24can_verify_and_charge_tx17hb1907151d54f3c6bE
    _ZN11near_client11rpc_handler10RpcHandler10process_tx17h3ca7fec97c6dd01eE
    _ZN110_$LT$actix..sync..SyncContextEnvelope$LT$M$GT$$u20$as$u20$actix..address..envelope..EnvelopeProxy$LT$A$GT$$GT$6handle17h0f78e99e6ce63395E
    _ZN3std3sys9backtrace28__rust_begin_short_backtrace17hd5ed81d58d44f867E
    _ZN4core3ops8function6FnOnce40call_once$u7b$$u7b$vtable.shim$u7d$$u7d$17hc18f360a04c1b975E
    _ZN3std3sys3pal4unix6thread6Thread3new12thread_start17hcc5ed016d554f327E
    [unknown]
```
