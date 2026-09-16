### Title
Panic via unbounded array-index write in `BytesAsHex::deserialize` when decoding non-human-readable transaction fields - (File: crates/starknet_api/src/serde_utils.rs)

### Summary
`BytesAsHex<N, PREFIXED>`, used to represent fixed-size hashes/addresses (`ChainId`, `ClassHash`, `ContractAddress`, transaction hash fields, etc. in `crates/starknet_api/src/core.rs`, `block.rs`, and `transaction/fields.rs`), implements a custom `Deserialize` whose binary-format branch (`visit_seq`) writes into a fixed `[u8; N]` buffer using an unchecked, attacker-controlled sequence length.

### Finding Description
`BytesAsHex::deserialize` dispatches on `deserializer.is_human_readable()`. For non-human-readable (binary, e.g. bincode) formats it calls `deserializer.deserialize_tuple(N, ByteArrayVisitor)`, whose `visit_seq` implementation is: [1](#0-0) 
The loop increments `i` and writes `res[i] = value` for every element the `SeqAccess` yields, with no check that `i < N`. A binary payload that encodes more than `N` elements for this field will cause `res[i]` to index out of bounds, triggering a Rust panic (since array indexing bounds are always checked, this is a controlled crash rather than true memory corruption, but it is directly analogous to the CVE's OOB read in that attacker-controlled length data drives an unchecked buffer access).

This is analogous to the FreeImage CVE's `ReadInt32` OOB read: both trust an untrusted length/element-count value to drive a fixed-size buffer read/write without validating it against the buffer capacity.

### Impact Explanation
If reached with a malformed binary payload, this crashes the deserializing process. `BytesAsHex` backs core identifiers (`ChainId`, `ClassHash`, `ContractAddress`, `TransactionHash`, etc.) used throughout `starknet_api`. If any component that receives untrusted binary-serialized data reachable from a single transaction sender (e.g., an internal bincode-encoded queue/IPC path carrying an `InternalRpcTransaction` or its constituent types) deserializes it through this code path, an attacker-crafted payload could crash that process, which could halt block building/transaction processing (a form of denial of service against the sequencer's liveness).

### Likelihood Explanation
I could **not confirm** a concrete unprivileged-transaction-sender-reachable path that exercises the non-human-readable (`deserialize_tuple`) branch of `BytesAsHex`. All directly observed usages of RPC/JSON-RPC transaction ingestion (`deserialize_transaction_json_to_starknet_api_tx` in the same file) use `serde_json`, which is human-readable and takes the hex-string branch (`bytes_from_hex_str`), which does perform a length check. The binary branch would only be hit by a non-human-readable `Deserializer` (e.g., bincode) applied to a `BytesAsHex`-containing struct; I found protobuf-based (not `serde`-tuple-based) encodings for mempool P2P propagation and RPC-transaction-to-bytes conversion (`RpcTransactionBatch`/protobuf `MempoolTransactionBatch`), and no confirmed code path where a struct containing `BytesAsHex` is deserialized with `bincode` (or another non-human-readable `serde::Deserializer`) from attacker-supplied bytes originating from a single submitted transaction, contract call, declared class, or L1 message, as opposed to internal trusted storage/DB round-trips or P2P/peer paths (explicitly out of scope). Given the "Reject ... p2p/sync/catchup ... dependency-only bugs" scoping rule and the lack of confirmed reachability from an unprivileged sender within scope, likelihood is low/unconfirmed.

### Recommendation
Regardless of current reachability, harden `ByteArrayVisitor::visit_seq` to bound-check writes and reject sequences with a length other than exactly `N`, e.g.:
```rust
fn visit_seq<A>(self, mut seq: A) -> Result<Self::Value, A::Error>
where
    A: serde::de::SeqAccess<'de>,
{
    let mut res = [0u8; N];
    for i in 0..N {
        res[i] = seq.next_element()?.ok_or_else(|| serde::de::Error::invalid_length(i, &self))?;
    }
    if seq.next_element::<u8>()?.is_some() {
        return Err(serde::de::Error::invalid_length(N + 1, &self));
    }
    Ok(BytesAsHex(res))
}
```
This removes any possibility of an out-of-bounds write regardless of which deserialization backend or transport eventually calls into this code, and matches the existing length validation already present in the human-readable (`bytes_from_hex_str`) branch.

### Proof of Concept
Not independently reproducible from this analysis because a concrete attacker-controlled call site invoking the non-human-readable branch of `BytesAsHex::deserialize` with sender-controlled data was not located in the reachable code paths. A minimal unit-level reproduction (not tied to the sequencer's transaction-processing pipeline) would be to bincode-serialize a sequence of more than `N` bytes and deserialize it as `BytesAsHex<N, _>`, which panics on `res[i] = value` once `i >= N`. [2](#0-1) [3](#0-2)

### Citations

**File:** crates/starknet_api/src/serde_utils.rs (L27-65)
```rust
pub struct BytesAsHex<const N: usize, const PREFIXED: bool>(pub(crate) [u8; N]);

impl<'de, const N: usize, const PREFIXED: bool> Deserialize<'de> for BytesAsHex<N, PREFIXED> {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        struct ByteArrayVisitor<const N: usize, const PREFIXED: bool>;
        impl<'de, const N: usize, const PREFIXED: bool> Visitor<'de> for ByteArrayVisitor<N, PREFIXED> {
            type Value = BytesAsHex<N, PREFIXED>;

            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("a byte array")
            }

            fn visit_seq<A>(self, mut seq: A) -> Result<Self::Value, A::Error>
            where
                A: serde::de::SeqAccess<'de>,
            {
                let mut res = [0u8; N];
                let mut i = 0;
                while let Some(value) = seq.next_element()? {
                    res[i] = value;
                    i += 1;
                }
                Ok(BytesAsHex(res))
            }
        }

        if deserializer.is_human_readable() {
            let s = String::deserialize(deserializer)?;
            bytes_from_hex_str::<N, PREFIXED>(s.as_str())
                .map_err(serde::de::Error::custom)
                .map(BytesAsHex)
        } else {
            deserializer.deserialize_tuple(N, ByteArrayVisitor)
        }
    }
}
```

**File:** crates/starknet_api/src/serde_utils.rs (L107-134)
```rust
/// Deserializes a Hex decoded as string to a byte array.
pub fn bytes_from_hex_str<const N: usize, const PREFIXED: bool>(
    hex_str: &str,
) -> Result<[u8; N], InnerDeserializationError> {
    let hex_str = if PREFIXED {
        hex_str
            .strip_prefix("0x")
            .ok_or(InnerDeserializationError::MissingPrefix { hex_str: hex_str.into() })?
    } else {
        hex_str
    };

    // Make sure string is not too long.
    if hex_str.len() > 2 * N {
        let mut err_str = "0x".to_owned();
        err_str.push_str(hex_str);
        return Err(InnerDeserializationError::BadInput {
            expected_byte_count: N,
            string_found: err_str,
        });
    }

    // Pad if needed.
    let to_add = 2 * N - hex_str.len();
    let padded_str = vec!["0"; to_add].join("") + hex_str;

    Ok(hex::decode(padded_str)?.try_into().expect("Unexpected length of deserialized hex bytes."))
}
```
