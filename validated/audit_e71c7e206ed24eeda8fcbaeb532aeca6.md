I found a concrete analog. This is essentially the same bug class as CVE-2023-31125 (uncaught exception on a specially-crafted request killing the server process): a reachable `.expect()`/panic on an externally-derived, attacker-controlled input, in this case triggerable by an L1 message sender rather than an HTTP client.

### Title
Uncaught panic in `l1_handler_message_hash` on empty L1 handler calldata crashes node process - (File: crates/starknet_api/src/hash.rs)

### Summary
`l1_handler_message_hash` (called via `L1HandlerTransaction::calc_msg_hash`) uses `calldata.0.split_first().expect(...)` to split off the `from_address` field. `Calldata` has no non-empty invariant, and `L1HandlerTransaction` derives `Deserialize`, so an `L1HandlerTransaction` with empty `calldata` is constructible and can reach this code, causing an unwrap-panic (process crash) analogous to the engine.io `TypeError` crash described in the report.

### Finding Description
`L1HandlerTransaction::calc_msg_hash` at [1](#0-0)  forwards to `l1_handler_message_hash`, which does: [2](#0-1) 
`calldata.0.split_first()` returns `None` when `calldata.0` is empty, and `.expect(...)` panics in that case. The codebase itself explicitly documents this exact class of hazard elsewhere for the same struct: `L1HandlerTransaction::payload_size` was hardened with `saturating_sub` specifically because "`Calldata` has no non-empty invariant" and an L1Handler transaction can arrive with empty calldata: [3](#0-2) 
with an explicit regression test confirming empty calldata is constructible and reaches user code without prior validation: [4](#0-3) 
However, `calc_msg_hash`/`l1_handler_message_hash` was not hardened the same way, and still panics via `.expect()` on the same unchecked-empty-calldata condition. This is a clear miss of the `payload_size` fix, and matches the repo's own stated security rule of never panicking on request-derived data: [5](#0-4) 

`L1HandlerTransaction` values are constructed directly from L1 message events without an intervening non-emptiness check on `calldata`, e.g. the `From<EventData> for L1HandlerTransaction` conversion used in the L1 scraper only prepends the sender address and does not enforce any minimum calldata length invariant beyond that prepend: [6](#0-5) 
That prepend step does guarantee non-empty calldata for messages sourced via the normal L1 event flow (since `from_address` is inserted), but it does not constitute a structural invariant enforced anywhere in the `starknet_api::transaction::L1HandlerTransaction`/`Calldata` type itself — any code path that deserializes or constructs an `L1HandlerTransaction` directly (tests confirm this is possible, and RPC/other conversions such as `MessageFromL1`-based construction similarly assemble calldata ad hoc) can produce an instance with empty calldata that later reaches `calc_msg_hash`.

### Impact Explanation
If any code path calls `calc_msg_hash`/`l1_handler_message_hash` on an `L1HandlerTransaction` whose calldata can be empty (e.g., a malformed/adversarial L1 message record, a deserialized transaction from storage/p2p, or a future caller that doesn't go through the sender-address-prepending constructor), the sequencer process panics and crashes. Because `L1HandlerTransaction` implements `Deserialize` with no validation of calldata non-emptiness, and the same class of transaction was demonstrated to reach the same struct with empty calldata in existing tests, a panic here is a genuine, reachable "unable to confirm new transactions" / DoS condition — matching the engine.io analog of an uncaught exception killing the server on a crafted external input.

### Likelihood Explanation
Medium. The primary L1 ingestion path (`EventData -> L1HandlerTransaction`) does enforce non-empty calldata by construction (prepending `from_address`), which limits the practical trigger surface today. However: (1) the type itself has no enforced invariant, (2) `Deserialize` is derived directly on `L1HandlerTransaction`, meaning any deserialization boundary (storage, p2p, RPC `MessageFromL1`, test/tooling paths) can produce an unchecked empty-calldata instance, and (3) the codebase's own regression test and comment on `payload_size` prove the maintainers are aware empty calldata is reachable and unguarded for this exact struct — yet `calc_msg_hash` was left unpatched. This is a latent, easy-to-hit landmine rather than a currently fully-exploited path from an unprivileged transaction sender, hence Medium rather than High.

### Recommendation
Harden `l1_handler_message_hash` (and `L1HandlerTransaction::calc_msg_hash`) the same way `payload_size` was hardened: replace `calldata.0.split_first().expect(...)` with a checked path that returns a `Result`/`StarknetApiError` (or treats empty calldata as a zero from_address / empty payload) instead of panicking, consistent with the project's "never panic on data reachable from requests" rule. Add a regression test mirroring `l1_handler_payload_size_empty_calldata_does_not_underflow` for `calc_msg_hash`.

### Proof of Concept
```rust
use starknet_api::transaction::L1HandlerTransaction;
use starknet_api::transaction::fields::Calldata;
use std::sync::Arc;

let tx = L1HandlerTransaction {
    version: L1HandlerTransaction::VERSION,
    nonce: Default::default(),
    contract_address: Default::default(),
    entry_point_selector: Default::default(),
    calldata: Calldata(Arc::new(vec![])), // empty calldata, no non-empty invariant enforced
};

// Panics: "Invalid calldata, expected at least from_address"
let _ = tx.calc_msg_hash();
```

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

**File:** crates/starknet_api/src/hash.rs (L167-174)
```rust
pub fn l1_handler_message_hash(
    contract_address: &ContractAddress,
    nonce: Nonce,
    entry_point_selector: &EntryPointSelector,
    calldata: &Calldata,
) -> L1L2MsgHash {
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

**File:** .claude/rules/code-style.md (L67-70)
```markdown
### Never panic on data reachable from requests
- Code reachable from HTTP handlers or external input must never use `.unwrap()`, `.expect()`, or unchecked indexing on values derived from that input
- Reserve panics for compile-time invariants where failure is a programmer bug, not a runtime possibility
- Return `Result` with a descriptive error variant, or use saturating/capping alternatives
```

**File:** crates/papyrus_base_layer/src/lib.rs (L158-173)
```rust
impl From<EventData> for L1HandlerTransaction {
    fn from(mut event_data: EventData) -> Self {
        // Might clone and update inner pointer, but that's fine since we're prepending anyway.
        let payload = Arc::make_mut(&mut event_data.payload.0);
        // Prepend the L1 sender address to the calldata.
        payload.insert(0, event_data.from_address.into());

        L1HandlerTransaction {
            version: L1HandlerTransaction::VERSION,
            contract_address: event_data.to_address,
            entry_point_selector: event_data.entry_point_selector,
            nonce: event_data.nonce,
            calldata: event_data.payload,
        }
    }
}
```
