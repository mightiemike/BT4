## Title
Unbounded Borsh-decode allocation in JSON-RPC transaction submission - (File: chain/jsonrpc/src/api/transactions.rs)

### Summary
The JSON-RPC entry points that accept a `signed_tx_base64` payload (`send_tx`, `broadcast_tx_async`, `broadcast_tx_commit`, `tx`) decode the caller-supplied bytes directly with `SignedTransaction::try_from_slice` with no pre-decode size gate, unlike the equivalent path in the P2P layer, which was explicitly hardened against this exact class of bug.

### Finding Description
`decode_signed_transaction` in [1](#0-0)  base64-decodes the RPC caller's string and immediately calls `SignedTransaction::try_from_slice(&bytes)` — there is no check on `bytes.len()` and no cap on the length fields that Borsh will read out of the buffer before the runtime's `max_transaction_size` validation ever runs (that check only happens later, during `validate_transaction`, after the value has already been fully materialized).

This is the same `SignedTransaction` type that the P2P layer explicitly guards against: `chain/network/src/network_protocol/proto_conv/peer_message.rs` introduces `MAX_TRANSACTION_SIZE_BYTES` and routes decoding through `try_from_slice_with_limit` specifically because "a maliciously inflated transaction[can] exhaust... node memory at decode time" [2](#0-1) , and the decode call site is annotated "Bound the decode-time allocation: see `MAX_TRANSACTION_SIZE_BYTES`" [3](#0-2) . The generic helper backing that gate, `try_from_slice_with_limit`, documents exactly the mechanism being defended against: "a maliciously inflated peer payload cannot force a large allocation at decode time... for any peer-supplied borsh blob whose decoded form can be much larger than its wire size" [4](#0-3) .

The JSON-RPC HTTP layer only limits the overall request body via `RpcLimitsConfig::json_payload_max_size` (default 10 MiB) [5](#0-4) . That cap bounds wire size only; it does nothing to stop a small, well-formed borsh buffer from declaring an oversized `Vec` length prefix inside the transaction's nested fields (e.g. `args`/`method_names` in `FunctionCall`/`AddKey` actions), which is precisely the amplification the network-layer fix targets. A crafted `signed_tx_base64` value can therefore drive a large decode-time allocation attempt before any `max_transaction_size`/action-limit validation runs, because that validation is performed on the already-decoded `SignedTransaction`, not on the raw bytes.

### Impact Explanation
Any unauthenticated JSON-RPC caller reaching `send_tx`/`broadcast_tx_async`/`broadcast_tx_commit`/`tx` can trigger this path without needing a valid signature, an existing account, or gas payment, since the decode happens before any of those checks. A successful trigger causes excessive memory allocation on the RPC/validator node handling the request, which can degrade or crash the node — a transaction-triggered denial of service impacting node availability.

### Likelihood Explanation
The JSON-RPC transaction-submission endpoints are the primary, always-open path for submitting transactions to a NEAR node and require no authentication or prior state. The only friction is the 10 MiB payload cap, which does not meaningfully constrain this specific bug class since the amplification comes from the ratio between a small length prefix and a much larger implied allocation, not from the total payload size.

### Recommendation
Apply the same defense used in `chain/network/src/network_protocol/proto_conv/peer_message.rs`: reject the base64-decoded bytes in `decode_signed_transaction` when they exceed a bound consistent with `max_transaction_size` (or reuse `try_from_slice_with_limit`) before calling `SignedTransaction::try_from_slice`, so oversized/maliciously nested payloads are rejected prior to decode-time allocation.

### Proof of Concept
1. Craft a `Transaction`/`SignedTransaction` borsh byte sequence where a nested `Vec<u8>` (e.g., a `FunctionCall` action's `args`, or `AddKey`'s `method_names`) declares a length near `u32::MAX` while the buffer itself remains small.
2. Base64-encode the crafted bytes and send it as `signed_tx_base64` to the `send_tx` (or `broadcast_tx_async`/`broadcast_tx_commit`/`tx`) JSON-RPC method, staying under the 10 MiB `json_payload_max_size`.
3. The node calls `decode_signed_transaction` → `SignedTransaction::try_from_slice(&bytes)` [1](#0-0) , which attempts to materialize the oversized `Vec` before `max_transaction_size`/action-size validation ever executes, exhausting memory on the handling node.

### Citations

**File:** chain/jsonrpc/src/api/transactions.rs (L71-76)
```rust
fn decode_signed_transaction(value: String) -> Result<SignedTransaction, RpcParseError> {
    let bytes = near_primitives::serialize::from_base64(&value)
        .map_err(|err| RpcParseError(format!("Failed to decode transaction: {}", err)))?;
    SignedTransaction::try_from_slice(&bytes)
        .map_err(|err| RpcParseError(format!("Failed to decode transaction: {}", err)))
}
```

**File:** chain/network/src/network_protocol/proto_conv/peer_message.rs (L23-28)
```rust
/// Upper bound on the Borsh-encoded size of a peer-supplied transaction body,
/// enforced before the body is deserialized to mitigate maliciously inflated
/// transactions exhausting node memory at decode time. Set comfortably above
/// the largest valid transaction (`max_transaction_size`, historically 4 MiB)
/// and far below the 512 MiB peer-frame cap.
const MAX_TRANSACTION_SIZE_BYTES: usize = 16 * MIB as usize;
```

**File:** chain/network/src/network_protocol/proto_conv/peer_message.rs (L474-478)
```rust
            ProtoMT::Transaction(t) => PeerMessage::Transaction(
                // Bound the decode-time allocation: see `MAX_TRANSACTION_SIZE_BYTES`.
                try_from_slice_with_limit(&t.borsh, MAX_TRANSACTION_SIZE_BYTES)
                    .map_err(Self::Error::Transaction)?,
            ),
```

**File:** chain/network/src/network_protocol/proto_conv/util.rs (L14-28)
```rust
/// Borsh-deserializes `T` from `bytes`, rejecting inputs larger than `limit`
/// before decoding so a maliciously inflated peer payload cannot force a large
/// allocation at decode time. Use this for any peer-supplied borsh blob whose
/// decoded form can be much larger than its wire size; `limit` must sit above
/// the largest legitimate encoding of `T` and well below the peer-frame cap.
/// Returns `io::Error` so it composes with the borsh-based proto decode sites.
pub fn try_from_slice_with_limit<T: BorshDeserialize>(bytes: &[u8], limit: usize) -> io::Result<T> {
    if bytes.len() > limit {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("encoded size {} exceeds the limit of {limit} bytes", bytes.len()),
        ));
    }
    T::try_from_slice(bytes)
}
```

**File:** chain/jsonrpc/src/lib.rs (L145-154)
```rust
#[derive(serde::Serialize, serde::Deserialize, Clone, Debug)]
pub struct RpcLimitsConfig {
    /// Maximum byte size of the json payload.
    pub json_payload_max_size: usize,
}

impl Default for RpcLimitsConfig {
    fn default() -> Self {
        Self { json_payload_max_size: 10 * 1024 * 1024 }
    }
```
