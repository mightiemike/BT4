### Title
Unguarded `.expect()` on empty L1-handler calldata causes an unrecoverable panic when computing the L1→L2 message hash - (File: `crates/starknet_api/src/hash.rs`)

### Summary
`l1_handler_message_hash` extracts the "from address" field from an `L1HandlerTransaction`'s calldata by calling `calldata.0.split_first().expect(...)` without first checking that the calldata is non-empty [1](#0-0) . This mirrors the reported bug class: extracting a fixed field from a variable-length input array without a prior length check, so a malformed/adversarial input causes the function to fail via a panic rather than a graceful, informative error.

### Finding Description
`L1HandlerTransaction` (the executable transaction wrapper) derives `Deserialize` and its `calldata: Calldata` field carries no non-empty invariant, so it is directly constructible with empty calldata [2](#0-1) . The codebase already had to defensively patch one function (`payload_size`) against this exact scenario, explicitly noting the underflow risk in a comment: "Calldata has no non-empty invariant... an empty calldata is constructible" [3](#0-2) .

However, `l1_handler_message_hash` — used by `L1HandlerTransaction::calc_msg_hash` [4](#0-3)  — was **not** patched with the same defensive guard and still panics via `.expect("Invalid calldata, expected at least from_address")` on empty calldata [5](#0-4) .

This function is reachable from RPC endpoints that compute the L1↔L2 message hash for a given transaction, as confirmed by usages in `crates/apollo_rpc/src/v0_8/api/api_impl.rs`. Because the calldata for an `L1HandlerTransaction` is nominally supposed to always contain the L1 sender address as its first element (this is enforced by convention in the two "trusted" construction paths — `From<EventData> for L1HandlerTransaction` in `crates/papyrus_base_layer/src/lib.rs` and `From<MessageFromL1> for L1HandlerTransaction` in `crates/apollo_rpc/src/v0_8/transaction.rs`, both of which always prepend the sender address — but nothing in the type system or a deserialization-time validator enforces this invariant globally.

### Impact Explanation
A panic in a widely-shared, no-`unwrap`-tolerant crate (`starknet_api`) that is linked into RPC-serving and potentially execution-adjacent binaries can crash or otherwise disrupt the serving process handling the request (denial-of-service on that component) whenever it is asked to compute a message hash for a transaction that was deserialized with an empty/malformed calldata, rather than returning a typed error. This does not by itself cause loss of funds or wrong committed state, but it is a crash-on-malformed-input bug in a security-sensitive data structure central to L1↔L2 messaging.

### Likelihood Explanation
Likelihood depends on whether any reachable ingress path allows deserializing/constructing an `L1HandlerTransaction`/`Calldata` with zero elements before `calc_msg_hash` is invoked (e.g., via RPC JSON deserialization of an `L1HandlerTransaction` object, or a crafted transaction object passed to an endpoint that calls `calc_msg_hash`). The repository's own regression test for `payload_size` demonstrates the team is aware empty calldata is constructible via `Deserialize`, which raises the likelihood that the same unguarded case is reachable for `l1_handler_message_hash` too. I was not able to fully confirm, within the scope of the search tools available, a concrete unauthenticated caller path into `calc_msg_hash` with attacker-supplied empty calldata (it depends on `apollo_rpc`'s specific endpoint semantics) — this should be verified with the full source (see note below).

### Recommendation
Mirror the guard already applied to `payload_size`: validate/require `calldata.0` non-empty and return a `Result`/typed error (e.g. `StarknetApiError`) instead of panicking in `l1_handler_message_hash`, e.g.:
```rust
let (from_address, payload) = calldata
    .0
    .split_first()
    .ok_or(StarknetApiError::...)?;
```
and propagate this as a `Result` through `calc_msg_hash` and its callers, consistent with the "never panic on data reachable from requests" guidance already documented in the repo's own coding rules [6](#0-5) .

### Proof of Concept
Construct an `L1HandlerTransaction` (executable or RPC variant) with `calldata: Calldata(Arc::new(vec![]))` and call `.calc_msg_hash()` (or route it to whatever RPC endpoint in `crates/apollo_rpc/src/v0_8/api/api_impl.rs` invokes `l1_handler_message_hash`/`calc_msg_hash`). The `.expect("Invalid calldata, expected at least from_address")` at `crates/starknet_api/src/hash.rs:174` panics instead of returning an error, analogous to the existing regression test that had to be added for `payload_size` with the same empty-calldata scenario [7](#0-6) .

Note: I could not fully verify, with the tools available, that an external/unprivileged caller can reach `calc_msg_hash` with attacker-controlled empty calldata through a live RPC request (this requires inspecting the full `apollo_rpc` endpoint handler logic, which was only partially visible in the index). If precise confirmation of this ingress path is needed, a full source review (e.g., via a Devin session) of `crates/apollo_rpc/src/v0_8/api/api_impl.rs` is recommended.

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

**File:** crates/starknet_api/src/hash.rs (L167-175)
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

**File:** .claude/rules/code-style.md (L67-70)
```markdown
### Never panic on data reachable from requests
- Code reachable from HTTP handlers or external input must never use `.unwrap()`, `.expect()`, or unchecked indexing on values derived from that input
- Reserve panics for compile-time invariants where failure is a programmer bug, not a runtime possibility
- Return `Result` with a descriptive error variant, or use saturating/capping alternatives
```
