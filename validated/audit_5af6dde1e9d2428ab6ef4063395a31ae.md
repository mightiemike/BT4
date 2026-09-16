Based on my investigation, the strongest analog to the KVM out-of-bounds panic is a `.expect()`-based panic in `l1_handler_message_hash` triggered by an empty `Calldata` on an `L1HandlerTransaction`, reachable from an unprivileged L1 message sender / RPC caller.

### Title
Panic on empty calldata in `l1_handler_message_hash` when computing L1-to-L2 message hash - (File: crates/starknet_api/src/hash.rs)

### Summary
`l1_handler_message_hash` unconditionally calls `calldata.0.split_first().expect("Invalid calldata, expected at least from_address")` [1](#0-0)  on the `L1HandlerTransaction`'s `calldata` field, panicking if the calldata vector is empty. This mirrors the KVM bug class where an externally-controlled index/length assumption (there: `guest_irq` bound; here: calldata's minimum length) is not validated before an unchecked access, causing a crash.

### Finding Description
`L1HandlerTransaction::calc_msg_hash` delegates directly to `l1_handler_message_hash`, which assumes calldata always contains at least the `from_address` element and destructures it with `.expect(...)` rather than returning a `Result` [2](#0-1) . The codebase is otherwise aware of the "empty L1Handler calldata" edge case — `L1HandlerTransaction::payload_size()` in `executable_transaction.rs` explicitly guards against it with `saturating_sub(1)`, and a dedicated regression test documents that `Calldata` has no non-empty invariant and that `L1HandlerTransaction` derives `Deserialize`, so an empty calldata is constructible [3](#0-2) [4](#0-3) . `calc_msg_hash`/`l1_handler_message_hash` was not updated with the same guard, leaving an inconsistent invariant enforcement between the two functions that both operate on the same untrusted `calldata` field.

### Impact Explanation
A panic on this path (called from RPC transaction-receipt/message-hash computation and from the L1 scraper/events pipeline) would crash the node process handling the request. If this code path can be exercised as part of common per-transaction processing (e.g. RPC callers requesting message hash for an L1Handler transaction with attacker/malformed-controlled `calldata`), it can cause denial of service on a sequencer or RPC node.

### Likelihood Explanation
I could not fully verify, within the available tool budget, whether an empty-calldata `L1HandlerTransaction` can actually reach `calc_msg_hash` through the production ingestion path (L1 event scraping always prepends `from_address`, so legitimately scraped L1 messages should never have empty calldata). The reachability is confirmed only for arbitrary/deserialized `L1HandlerTransaction` values (as shown by the existing unit test constructing one with empty calldata directly) and for RPC methods that call `calc_msg_hash` in `apollo_rpc/src/v0_8/api/api_impl.rs`, whose exact input-trust boundary I was unable to inspect before running out of iterations.

### Recommendation
Change `l1_handler_message_hash` to return a `Result` (or otherwise validate `calldata` non-emptiness before destructuring) instead of using `.expect()`, consistent with the `saturating_sub`-based guard already used in `payload_size()`, and propagate an error to callers rather than panicking.

### Proof of Concept
Construct an `L1HandlerTransaction` (executable or RPC variant) with `calldata: Calldata(Arc::new(vec![]))` — as already done in the existing test `l1_handler_payload_size_empty_calldata_does_not_underflow` [5](#0-4)  — and call `.calc_msg_hash()` on it; this triggers `calldata.0.split_first().expect(...)` and panics [1](#0-0) .

**Confidence caveat:** I was not able to confirm, within available search iterations, an end-to-end path from an *unprivileged external caller* (RPC request or malformed L1 message) supplying empty calldata all the way into `calc_msg_hash` in production code — only that the type system permits constructing such a value and that a sibling function (`payload_size`) was patched for exactly this scenario while `calc_msg_hash`/`l1_handler_message_hash` was not. A Devin session with full repo/terminal access would be needed to trace the RPC/l1-events call sites (`apollo_rpc/src/v0_8/api/api_impl.rs`, `apollo_l1_events/src/l1_events_provider.rs`, `apollo_l1_events/src/l1_scraper.rs`) to confirm exploitability versus this merely being defensive/dead code.

### Citations

**File:** crates/starknet_api/src/hash.rs (L154-163)
```rust
impl L1HandlerTransaction {
    pub fn calc_msg_hash(&self) -> L1L2MsgHash {
        l1_handler_message_hash(
            &self.contract_address,
            self.nonce,
            &self.entry_point_selector,
            &self.calldata,
        )
    }
}
```

**File:** crates/starknet_api/src/hash.rs (L173-174)
```rust
    let (from_address, payload) =
        calldata.0.split_first().expect("Invalid calldata, expected at least from_address");
```

**File:** crates/starknet_api/src/executable_transaction.rs (L399-405)
```rust
    pub fn payload_size(&self) -> usize {
        // The calldata includes the "from" field, which is not a part of the payload.
        // `saturating_sub` guards the empty-calldata case (which would otherwise underflow to
        // `usize::MAX` in release): `L1HandlerTransaction` derives `Deserialize` and `Calldata`
        // has no non-empty invariant.
        self.tx.calldata.0.len().saturating_sub(1)
    }
```

**File:** crates/starknet_api/src/executable_transaction_test.rs (L24-38)
```rust
/// `Calldata` has no non-empty invariant and `L1HandlerTransaction` derives `Deserialize`, so an
/// empty calldata is constructible. `payload_size` must not underflow (debug panic / release wrap
/// to usize::MAX); the payload of an empty calldata is just 0.
#[test]
fn l1_handler_payload_size_empty_calldata_does_not_underflow() {
    let tx = RpcL1HandlerTransaction {
        version: TransactionVersion::ZERO,
        nonce: Nonce::default(),
        contract_address: Default::default(),
        entry_point_selector: Default::default(),
        calldata: Calldata(Arc::new(vec![])),
    };
    let executable_tx =
        L1HandlerTransaction { tx, tx_hash: TransactionHash::default(), paid_fee_on_l1: Fee(0) };
    assert_eq!(executable_tx.payload_size(), 0);
```
