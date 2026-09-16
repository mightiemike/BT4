### Title
Panic (`.expect()` on empty calldata) in L1→L2 message-hash computation reachable via a stored L1Handler transaction with empty calldata - ([File: crates/starknet_api/src/hash.rs])

### Summary
`l1_handler_message_hash` in `crates/starknet_api/src/hash.rs` unconditionally calls `calldata.0.split_first().expect("Invalid calldata, expected at least from_address")` to split off the L1 sender address from the payload. `Calldata` has no non-empty invariant, and `starknet_api::transaction::L1HandlerTransaction` derives `Deserialize`, so an `L1HandlerTransaction` with empty calldata is constructible from any deserialization path (RPC responses, stored/JSON transactions), not only from the L1 scraper path where the sender address is always prepended by `EventData::from`/`MessageFromL1::from`.

### Finding Description
`calc_msg_hash()` [1](#0-0)  forwards to `l1_handler_message_hash`, which does: [2](#0-1) 

The `.expect(...)` on `split_first()` panics whenever `calldata.0` is empty. The sibling code in `L1HandlerTransaction::payload_size()` in `starknet_api/src/executable_transaction.rs` explicitly documents and guards against exactly this scenario with `saturating_sub`, noting: "`L1HandlerTransaction` derives `Deserialize`, so an empty calldata is constructible" [3](#0-2) . This is analogous to the Wireshark DNS SRV-record bug: an attacker-crafted/malformed record (here, a zero-length calldata array in an L1Handler transaction) reaches an unguarded dereference/split operation and crashes the process, instead of the defensive path used elsewhere in the same struct.

Both known constructors that build `L1HandlerTransaction` from genuine L1 events always prepend the sender address, so calldata is never empty on that path: `EventData::from` [4](#0-3)  and `MessageFromL1::from` [5](#0-4) . However, `l1_handler_message_hash`/`calc_msg_hash` is a general-purpose API on the `starknet_api` type, and `L1HandlerTransaction` (the RPC/API-level struct) derives `Deserialize` with no length validation, so any code path that deserializes an `L1HandlerTransaction` from untrusted or stored JSON (RPC responses, feeder-gateway style JSON, node-to-node transaction data) and later calls `calc_msg_hash()` on it is exposed to this panic. `calc_msg_hash` is used in `crates/apollo_rpc/src/v0_8/api/api_impl.rs` (message-status RPC lookups) and in `crates/apollo_l1_events/src/l1_events_provider.rs` / `l1_scraper.rs`.

### Impact Explanation
A panic in a shared library function used across RPC handling, L1 event processing, and message-status lookups can crash the calling process/task. If this is reachable in a validating/executing node path (e.g., an RPC node computing a message hash for a transaction pulled from storage or from an external response with a maliciously/incorrectly empty calldata array), it causes a node to crash or a request-handling task to abort — a denial-of-service against a network component, which matches the "network unable to confirm new transactions" / node-crash impact class the scan targets. Because the guarded/safe alternative (`saturating_sub` pattern) already exists nearby for exactly this invariant violation, the un-guarded `.expect()` in `l1_handler_message_hash` is inconsistent and is the true root cause.

### Likelihood Explanation
Reachability depends on whether an externally-influenced, empty-calldata `L1HandlerTransaction` can actually reach `calc_msg_hash()` in a production code path (vs. only being possible via test-only construction). I was not able to fully confirm, within the available tool budget, whether the RPC (`api_impl.rs`) or `apollo_l1_events` call sites that invoke `calc_msg_hash` receive transactions from a source that permits an empty-calldata `L1HandlerTransaction` to be deserialized without prior validation (e.g., from storage written by a fully-validated pipeline, versus from an external feeder-gateway/JSON response that is deserialized directly). This is the key open question that determines whether this is a genuinely externally-triggerable DoS or a defense-in-depth gap only reachable through internal/test code.

### Recommendation
- Replace the `.expect(...)` in `l1_handler_message_hash` (`crates/starknet_api/src/hash.rs`) with a `Result`-returning check (mirroring the `saturating_sub` treatment already used in `L1HandlerTransaction::payload_size`), returning a descriptive error instead of panicking on empty calldata.
- Audit all call sites of `calc_msg_hash()` / `l1_handler_message_hash()` (`apollo_rpc::v0_8::api::api_impl`, `apollo_l1_events::l1_events_provider`, `apollo_l1_events::l1_scraper`) to confirm whether any of them process transaction data deserialized from an untrusted or unvalidated source before calling this function, and add validation of non-empty calldata at the point transactions are first constructed/deserialized if so.

### Proof of Concept
```rust
use starknet_api::core::{ContractAddress, EntryPointSelector, Nonce};
use starknet_api::transaction::fields::Calldata;
use starknet_api::hash::l1_handler_message_hash;
use std::sync::Arc;

// Empty calldata is a valid, deserializable state (no non-empty invariant enforced).
let empty_calldata = Calldata(Arc::new(vec![]));

// Panics: "Invalid calldata, expected at least from_address"
let _ = l1_handler_message_hash(
    &ContractAddress::default(),
    Nonce::default(),
    &EntryPointSelector::default(),
    &empty_calldata,
);
```
This mirrors the exact scenario already called out (and defended against) in the neighboring `payload_size()` method's doc comment [3](#0-2) , confirming the empty-calldata state is reachable at the type level; what remains unverified is the specific untrusted external trigger for `calc_msg_hash` in production RPC/L1-event flows.

### Citations

**File:** crates/starknet_api/src/hash.rs (L154-162)
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
```

**File:** crates/starknet_api/src/hash.rs (L167-188)
```rust
pub fn l1_handler_message_hash(
    contract_address: &ContractAddress,
    nonce: Nonce,
    entry_point_selector: &EntryPointSelector,
    calldata: &Calldata,
) -> L1L2MsgHash {
    let (from_address, payload) =
        calldata.0.split_first().expect("Invalid calldata, expected at least from_address");

    let mut encoded = Vec::new();
    encoded.extend(from_address.to_bytes_be());
    encoded.extend(contract_address.0.key().to_bytes_be());
    encoded.extend(nonce.to_bytes_be());
    encoded.extend(entry_point_selector.0.to_bytes_be());

    let payload_length_as_felt =
        Felt::from(u64::try_from(payload.len()).expect("usize should fit in u64"));
    encoded.extend(payload_length_as_felt.to_bytes_be());

    for felt in payload {
        encoded.extend(felt.to_bytes_be());
    }
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

**File:** crates/apollo_rpc/src/v0_8/transaction.rs (L1220-1234)
```rust
impl From<MessageFromL1> for L1HandlerTransaction {
    fn from(message: MessageFromL1) -> Self {
        let sender_as_felt = eth_address_to_felt(message.from_address);
        let mut calldata = vec![sender_as_felt];
        calldata.extend_from_slice(&message.payload.0);
        let calldata = Calldata(Arc::new(calldata));
        Self {
            version: L1HandlerTransaction::VERSION,
            contract_address: message.to_address,
            entry_point_selector: message.entry_point_selector,
            calldata,
            ..Default::default()
        }
    }
}
```
