### Title
Panic-inducing NULL-pointer-equivalent unwrap on empty L1 handler calldata in `l1_handler_message_hash` - (File: `crates/starknet_api/src/hash.rs`)

### Summary
`L1HandlerTransaction::calc_msg_hash()` and the underlying `l1_handler_message_hash` helper call `calldata.0.split_first().expect("Invalid calldata, expected at least from_address")` on an untrusted `Calldata` field that has no guaranteed non-empty invariant. This mirrors the Squid CVE-2018-1000027 pattern: an attacker-influenced field that is assumed to always be present is dereferenced/split without validation, producing a crash (panic) instead of the NULL pointer dereference seen in Squid.

### Finding Description
`Calldata` is a plain `Arc<Vec<Felt>>` wrapper with no non-empty invariant, and `L1HandlerTransaction` derives `Deserialize`, so an `L1HandlerTransaction` (or the RPC-level `MessageFromL1`/broadcasted equivalent) with empty `calldata` is trivially constructible from external input: [1](#0-0) 

The developers were already aware of this exact class of bug and patched one call site (`payload_size`) with `saturating_sub`, explicitly noting: *"`Calldata` has no non-empty invariant"*: [1](#0-0) 

A regression test even codifies this exact concern for `payload_size`: [2](#0-1) 

However, the sibling function `l1_handler_message_hash` (invoked via `L1HandlerTransaction::calc_msg_hash`) was not fixed and still unconditionally unwraps: [3](#0-2) 

`calc_msg_hash` is referenced from the RPC layer (`crates/apollo_rpc/src/v0_8/api/api_impl.rs`), which handles messages/hashes derived from client-supplied `MessageFromL1`-style transaction data (e.g., for `estimate_message_fee`/message-hash related RPC calls). I was not able to fully confirm, within the remaining tool budget, the exact call chain proving that `api_impl.rs` passes a client-controlled, potentially-empty `calldata` all the way into `calc_msg_hash`/`l1_handler_message_hash` without an intervening non-empty check — this should be verified directly against `crates/apollo_rpc/src/v0_8/api/api_impl.rs` and `crates/apollo_rpc/src/v0_8/transaction.rs` (`MessageFromL1`).

### Impact Explanation
If reachable from an RPC endpoint or any other code path that accepts an externally-supplied `L1HandlerTransaction`/`MessageFromL1` with empty calldata prior to validating a minimum length, invoking `calc_msg_hash` will panic. Depending on the panic-handling context (async task vs. bare thread, and whether `catch_unwind` boundaries exist), this can range from an isolated RPC request failure to a crash of the serving component, which constitutes a Denial-of-Service condition — "a network unable to confirm new transactions" if it affects a component in the tx-processing/serving path.

### Likelihood Explanation
The precondition (empty `calldata`) is directly constructible via deserialization, as proven by the existing test for the sibling `payload_size` bug. The only open question is whether any validation layer rejects empty calldata for L1-handler-derived data before it reaches `calc_msg_hash`. Given that the analogous `payload_size` bug was found and fixed but `l1_handler_message_hash` was missed, it is plausible no such guard exists on this path either. Confirming exact caller-side validation in `apollo_rpc`/`apollo_l1_events`/`apollo_gateway` would be needed to elevate confidence to certain.

### Recommendation
- Add an explicit, non-panicking check for empty `calldata` in `l1_handler_message_hash` (and any other direct callers of `calldata.0.split_first()` on `L1HandlerTransaction`), returning a proper `Result`/error instead of `.expect(...)`.
- Audit all callers of `calc_msg_hash` (RPC handlers, L1 scraper, gateway/mempool paths) to ensure a minimum-calldata-length validation occurs before any L1-handler-derived data reaches hashing logic.
- Add a regression test mirroring `l1_handler_payload_size_empty_calldata_does_not_underflow` for `calc_msg_hash`/`l1_handler_message_hash` with empty calldata.

### Proof of Concept
1. Construct (or deserialize from an RPC request body) an `L1HandlerTransaction` with `calldata: Calldata(Arc::new(vec![]))`.
2. Call `.calc_msg_hash()` on it (directly, or via any RPC/API code path in `apollo_rpc` that passes external transaction data into it).
3. Execution panics at `calldata.0.split_first().expect("Invalid calldata, expected at least from_address")` in: [4](#0-3) 

**Note:** I could not fully verify within the available tool calls whether the RPC layer (`apollo_rpc/src/v0_8/api/api_impl.rs`) passes unvalidated, potentially-empty external calldata directly into this function without an intervening length check — this needs direct inspection of that file (and `crates/apollo_rpc/src/v0_8/transaction.rs`'s `MessageFromL1` type) to confirm end-to-end reachability from an untrusted RPC caller before treating this as fully proven versus a latent/defense-in-depth issue.

### Citations

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

**File:** crates/starknet_api/src/hash.rs (L154-176)
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

/// Calculating the message hash of L1 -> L2 message.
/// For more info: <https://docs.starknet.io/documentation/architecture_and_concepts/Network_Architecture/messaging-mechanism/#structure_and_hashing_l1-l2>
pub fn l1_handler_message_hash(
    contract_address: &ContractAddress,
    nonce: Nonce,
    entry_point_selector: &EntryPointSelector,
    calldata: &Calldata,
) -> L1L2MsgHash {
    let (from_address, payload) =
        calldata.0.split_first().expect("Invalid calldata, expected at least from_address");

    let mut encoded = Vec::new();
```
