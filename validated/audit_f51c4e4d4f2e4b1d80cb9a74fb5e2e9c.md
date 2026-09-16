Found a concrete analog. This confirms `estimate_message_fee` (JSON-RPC `starknet_estimateMessageFee`) accepts a `MessageFromL1` directly from any unauthenticated RPC caller and converts it via `L1HandlerTransaction::from(MESSAGE_FROM_L1.clone())` — the same `From<EventData>` conversion used by the L1 scraper — before executing it. Critically, that conversion always prepends the `from_address`, so via that specific path calldata can't be empty. However, `calc_msg_hash`/`l1_handler_message_hash` itself has no defensive check and `.expect()`s on `split_first()`, matching the CVE's pattern of an unchecked, attacker-influenced empty-length field feeding an unwrap/panic. I could not fully verify within the available iterations whether any additional code path constructs a `starknet_api::transaction::L1HandlerTransaction` with attacker-controlled empty `calldata` before `calc_msg_hash` is invoked (e.g., during OS re-execution message-hash verification against L1, or a devnet/test-only entry point) — that link is the missing piece needed to make this a real, unprivileged, remotely triggerable panic instead of a defense-in-depth gap.

### Title
Unchecked empty-calldata panic in `l1_handler_message_hash` (`split_first().expect(...)`) - ([File: crates/starknet_api/src/hash.rs])

### Summary
`l1_handler_message_hash` in `crates/starknet_api/src/hash.rs` (lines 167–193) computes the L1↔L2 message hash for an `L1HandlerTransaction` by calling `calldata.0.split_first().expect("Invalid calldata, expected at least from_address")`. [1](#0-0) 
`Calldata` has no non-empty invariant — it derives `Deserialize` and wraps a plain `Vec<Felt>` — so a zero-length calldata is constructible from any deserialized `L1HandlerTransaction`, mirroring the CVE's "zero-length attribute reaches unchecked sizing/indexing code" pattern.

### Finding Description
The bug class in the CVE is: a length-derived buffer/pointer operation that is correctly guarded for the general case but not for the zero-length case, leading to a `NULL`/`panic` when a subsequent operation assumes non-emptiness. The Rust analog here is `split_first()` returning `None` on an empty slice, `.expect(...)`'d into a panic. Note that the codebase is otherwise well aware of this exact hazard for `L1HandlerTransaction.calldata`: `L1HandlerTransaction::payload_size()` in `crates/starknet_api/src/executable_transaction.rs` explicitly uses `saturating_sub(1)` and documents that `Calldata` has "no non-empty invariant" and that `L1HandlerTransaction` derives `Deserialize`, guarding exactly this case: [2](#0-1) 
`l1_handler_message_hash`, however, was not hardened the same way and instead panics via `.expect()`. [3](#0-2) 

### Impact Explanation
If any reachable code path constructs a `starknet_api::transaction::L1HandlerTransaction` with empty `calldata` (bypassing the mandatory `from_address` prepend done in `EventData -> L1HandlerTransaction`, `crates/papyrus_base_layer/src/lib.rs:158-172`) and then calls `calc_msg_hash`/`l1_handler_message_hash` on it, the process panics. Depending on where this is invoked (e.g., a shared library call inside a request-handling thread without `catch_unwind`), this can crash or poison the calling process, which — if it occurs in consensus/OS message-hash verification against L1 — could cause a node to halt or diverge from honest peers, i.e. a network-availability impact within the rules' accepted categories ("network unable to confirm new transactions" / "honest-node divergence").

### Likelihood Explanation
Reachability is only partially confirmed. The one concrete external-input path found (`starknet_estimateMessageFee`, `crates/apollo_rpc/src/v0_8/api/api_impl.rs:1429-1506`, taking `MessageFromL1` from any unauthenticated RPC caller) converts through `EventData -> L1HandlerTransaction`, which always prepends `from_address`, so calldata cannot be empty on that specific path — this closes off the most obvious avenue. I was unable to confirm, within the remaining search budget, whether `calc_msg_hash` is also invoked on `L1HandlerTransaction` objects assembled elsewhere (e.g., via `starknet_api::transaction::L1HandlerTransaction` structs built directly from stored/FGW/OS-output data without the address prepend, as seen in `crates/apollo_starknet_client/src/reader/objects/transaction.rs:145-167` or `crates/native_blockifier/src/py_l1_handler.rs`) before this hash is computed for L1 message-consumption verification.

### Recommendation
Regardless of current reachability, harden `l1_handler_message_hash` to match the defensive pattern already used in `payload_size()`: return a `Result`/`StarknetApiError` (or treat empty calldata as `from_address = 0`, empty payload) instead of `.expect()`-panicking on `split_first()`. This removes the panic surface entirely and aligns the function with the codebase's own documented policy in `.claude/rules/code-style.md` ("Never panic on data reachable from requests"). [1](#0-0) 

### Proof of Concept
```rust
// Constructs an L1HandlerTransaction with empty calldata (no invariant prevents this)
// and calls the vulnerable hashing function directly.
let tx = starknet_api::executable_transaction::L1HandlerTransaction {
    tx: starknet_api::transaction::L1HandlerTransaction {
        version: TransactionVersion::ZERO,
        nonce: Nonce::default(),
        contract_address: ContractAddress::default(),
        entry_point_selector: EntryPointSelector::default(),
        calldata: Calldata(Arc::new(vec![])), // zero-length, mirrors the CVE's zero-length attribute
    },
    tx_hash: TransactionHash::default(),
    paid_fee_on_l1: Fee(0),
};
let _ = tx.calc_msg_hash(); // panics: "Invalid calldata, expected at least from_address"
```
This mirrors `l1_handler_payload_size_empty_calldata_does_not_underflow` in `crates/starknet_api/src/executable_transaction_test.rs:27-39`, which the codebase already added specifically to test-and-fix this exact empty-calldata hazard for `payload_size()` — `calc_msg_hash` was left unpatched. [4](#0-3)

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

**File:** crates/starknet_api/src/hash.rs (L172-175)
```rust
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

**File:** crates/starknet_api/src/executable_transaction_test.rs (L24-39)
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
}
```
