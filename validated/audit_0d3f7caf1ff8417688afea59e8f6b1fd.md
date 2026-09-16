Based on the investigation, I found a genuine confidentiality-impacting analog in the JSON-RPC read API, but I was unable to fully enumerate the exact 4 call sites in `crates/apollo_rpc/src/v0_8/api/api_impl.rs` that invoke `internal_server_error_with_msg` before running out of tool iterations. I flag this explicitly below.

### Title
Unauthenticated confidentiality leak: internal error details returned verbatim to JSON-RPC clients - ([File: crates/apollo_rpc/src/lib.rs])

### Summary
The Starknet JSON-RPC server (`apollo_rpc`) exposes two internal-error helpers. `internal_server_error` redacts the underlying error and returns only the generic `INTERNAL_ERROR_MSG` to the caller, but a sibling function, `internal_server_error_with_msg`, forwards `err.to_string()` — the full `Display` output of the internal error — directly into the JSON-RPC error object returned to the (unauthenticated) caller. [1](#0-0) 

### Finding Description
`internal_server_error_with_msg` is defined as:
```
fn internal_server_error_with_msg(err: impl std::fmt::Display) -> ErrorObjectOwned {
    error!("{}: {}", INTERNAL_ERROR_MSG, err);
    ErrorObjectOwned::owned(InternalError.code(), err.to_string(), None::<()>)
}
``` [2](#0-1) 

This is used in `crates/apollo_rpc/src/v0_8/api/api_impl.rs` (4 call sites, not fully enumerated in this session) to build RPC error responses. Unlike `internal_server_error`, which intentionally strips the error detail and only logs it server-side, this variant places the raw error's `Display` text into the `message` field of the JSON-RPC response body, which is sent back to any unauthenticated network caller of the RPC server. This mirrors the CVE's bug class: an unauthenticated attacker with plain HTTP/JSON-RPC access can trigger error paths (e.g., storage/state read failures, `StorageError::DBInconsistency`, or other backend errors surfaced through `RpcResult`) and receive internal diagnostic text in the response — potentially including storage/DB internals, state-read failure details, or other implementation specifics not intended for external consumers.

Notably, the codebase already has a hardened pattern for this exact concern elsewhere: `apollo_http_server`'s `errors.rs::serialize_error` explicitly regex-sanitizes and redacts error text before it reaches HTTP clients, and `StarknetError::internal_with_logging` in `apollo_gateway_types` deliberately replaces the error text with the literal string `"Internal error"` before returning it to callers. [3](#0-2) [4](#0-3) 

The RPC crate's own `internal_server_error` function follows the same safe pattern, which makes `internal_server_error_with_msg` an inconsistent, unsanitized escape hatch in the same crate.

### Impact Explanation
This matches the CVSS profile of the reference CVE (confidentiality-only, C:H/I:N/A:N, unauthenticated, network, low complexity): an unauthenticated JSON-RPC caller (any external Starknet API consumer — not a privileged operator/proposer/peer) can potentially extract internal error diagnostics (e.g., storage backend inconsistency messages, internal state-read failure context) that were only meant for server-side logs. Depending on which of the 4 call sites forward which underlying error types, this could disclose internal implementation details (DB layout, internal identifiers, or other non-public information) about the node to any caller — a genuine, if data-dependent, confidentiality impact reachable purely by making a normal RPC request (e.g., a `starknet_call`/state-read/estimateFee-style query), without needing to submit a transaction that changes state.

### Likelihood Explanation
High likelihood of reachability: JSON-RPC endpoints are unauthenticated and reachable by any network client per the "Networking Layer"/"JSON-RPC API" design of this sequencer. Triggering an internal error condition (e.g., a storage inconsistency, an edge case in state lookups) only requires crafting a request that hits one of the 4 call sites using `internal_server_error_with_msg`; I could not confirm from the available context exactly which request parameters trigger these specific error paths, so the practical ease of hitting each of the 4 sites is not fully verified in this pass.

### Recommendation
Audit all 4 call sites of `internal_server_error_with_msg` in `crates/apollo_rpc/src/v0_8/api/api_impl.rs`. For any call site whose underlying error can originate from internal subsystems (storage, class manager, state sync, DB) rather than being a deliberately-crafted user-facing message, replace the call with `internal_server_error` (which logs full detail server-side but returns only the generic `INTERNAL_ERROR_MSG` to the client), or apply the same sanitization pattern used in `apollo_http_server::errors::serialize_error` before including any error text in the client-facing response.

### Proof of Concept
1. Start a node with the JSON-RPC server (`apollo_rpc`) enabled and reachable on its configured port.
2. As an unauthenticated client, send a JSON-RPC request (e.g., `starknet_call`, `starknet_estimateFee`, or another read method) crafted or timed to trigger an internal error path that is wired through `internal_server_error_with_msg` in `api_impl.rs` (e.g., a storage-scope violation or a state-read/backend inconsistency).
3. Observe that the JSON-RPC error response's `message` field contains the raw `Display` output of the internal error (e.g., internal storage error text) rather than a generic `"Unknown error"` message — confirming the internal diagnostic leak to an unauthenticated caller.

Note: I was not able to fully read `crates/apollo_rpc/src/v0_8/api/api_impl.rs` in this session to enumerate the exact 4 call sites and confirm precisely which internal error types (and thus what sensitive content) flow through `internal_server_error_with_msg`. This should be verified with a follow-up code review of that file before treating severity as final.

### Citations

**File:** crates/apollo_rpc/src/lib.rs (L168-176)
```rust
fn internal_server_error(err: impl std::fmt::Display) -> ErrorObjectOwned {
    error!("{}: {}", INTERNAL_ERROR_MSG, err);
    ErrorObjectOwned::owned(InternalError.code(), INTERNAL_ERROR_MSG, None::<()>)
}

fn internal_server_error_with_msg(err: impl std::fmt::Display) -> ErrorObjectOwned {
    error!("{}: {}", INTERNAL_ERROR_MSG, err);
    ErrorObjectOwned::owned(InternalError.code(), err.to_string(), None::<()>)
}
```

**File:** crates/apollo_gateway_types/src/deprecated_gateway_error.rs (L76-79)
```rust
    pub fn internal_with_logging(log_message: &str, err: impl std::error::Error) -> Self {
        error!("Internal error: {log_message}: {err}.");
        Self { code: Self::internal_error_code(), message: "Internal error".to_string() }
    }
```

**File:** crates/apollo_http_server/src/errors.rs (L127-139)
```rust
fn serialize_error(error: &StarknetError) -> Response {
    let quote_re = Regex::new(r#"[\"`]"#).unwrap(); // " and ` => ' (single quote)
    let sanitize_re = Regex::new(r#"[^a-zA-Z0-9 :.,\[\]\(\)\{\}'_]"#).unwrap(); // All other non-alphanumeric characters except [:.,[](){}]_ => ' ' (space)

    let mut message = error.message.clone();
    message = quote_re.replace_all(&message, "'").to_string();
    message = sanitize_re.replace_all(&message, " ").to_string();

    let sanitized_error = StarknetError { code: error.code.clone(), message };

    serde_json::to_vec(&sanitized_error)
        .expect("Expecting a serializable StarknetError.")
        .into_response()
```
