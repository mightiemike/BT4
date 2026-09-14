### Title
JSON-RPC endpoint accepts arbitrarily deep nested JSON causing uncontrolled recursion / stack-overflow DoS - (File: chain/jsonrpc-primitives/src/message.rs)

### Summary
The near-jsonrpc server deserializes every inbound POST body directly into the `Message` enum via `axum::Json<Message>`, and `Message`/`Request`/`Notification` embed raw `serde_json::Value` for the `params` field, while `Message::Batch(Vec<Message>)` is itself recursively defined and deserialized with `#[serde(untagged)]`. Neither `serde_json::Value`'s recursive-descent parser nor the untagged `Message`/`Batch` deserialization enforces any nesting-depth limit, so an unauthenticated RPC caller can submit a single, size-limited (10MB) but deeply nested JSON payload (e.g. thousands of nested arrays or nested batches) and drive the parser into unbounded recursion, exhausting the stack and crashing/aborting the RPC-serving thread/process — the same CWE-674 uncontrolled-recursion bug class as CVE-2025-53864.

### Finding Description
The RPC HTTP handler `rpc_handler()` uses `Json<Message>` as an Axum extractor [1](#0-0) , which internally calls `serde_json` to deserialize the request body straight into the `Message` enum. `Message` is defined as an untagged enum whose variants include `Request`/`Notification` (both containing a bare `serde_json::Value params` field) and `Batch(Vec<Message>)`, which is self-referential [2](#0-1) . The `params: Value` fields use `serde_json::Value`'s standard `Deserialize` impl, and the `Batch` variant recurses into `Message` again.

`serde_json`'s deserializer (both for `Value` and for nested `Vec<Message>`/array/object structures) walks nested JSON structures via normal Rust function-call recursion with no built-in recursion-depth guard, unless the caller explicitly limits it (which nearcore does not do anywhere in this pipeline — `from_slice`/`from_str` call `serde_json::de::from_slice` directly with no depth limiting wrapper) [3](#0-2) . The only protection mentioned for the JSON-RPC endpoint is a flat request body size cap (documented as 10MB) [4](#0-3) ; that limit constrains total bytes but not nesting depth — a 10MB payload can trivially encode hundreds of thousands of nested `[` / `{` tokens (each opening bracket costs 1 byte), far exceeding the depth needed to overflow a typical thread stack (commonly a few thousand frames for `serde_json`/`Value` recursion).

This differs from other borsh-based recursion-guard work already present in the codebase (e.g., `NonDelegateAction`'s hand-written borsh deserializer explicitly rejects nested delegate actions to avoid recursion [5](#0-4) , and trie/flat-storage code deliberately uses iterative stacks instead of recursion to avoid stack overflows [6](#0-5) ) — no equivalent depth-limiting or iterative-parsing protection exists for the untrusted JSON ingested at the RPC boundary.

### Impact Explanation
A crash of the JSON-RPC server thread/process caused by a stack overflow is a denial of service against a node's public RPC endpoint. Depending on how the Rust runtime handles the stack overflow (SIGSEGV/abort), this can take down the entire `near` process serving JSON-RPC, not just the offending request's handler task, since stack overflows in Rust abort the process rather than being catchable as a panic. Because JSON-RPC endpoints are typically public and unauthenticated (`send_tx`, `query`, etc.), any external caller can trigger this with a single crafted HTTP POST, with no signed transaction or on-chain cost required.

### Likelihood Explanation
High likelihood of triggering a stack exhaustion given: (1) no authentication is required to reach the `/` JSON-RPC endpoint, (2) the endpoint accepts and fully parses arbitrary JSON bodies up to 10MB before any semantic validation occurs, (3) `serde_json`'s default (non-`arbitrary_precision`, non-depth-limited) parsing recurses per nesting level with no explicit safeguard in this codebase, and (4) constructing a payload with tens of thousands of nested `[` characters is trivial and fits comfortably within the stated body-size limit.

### Recommendation
- Enforce a maximum JSON nesting depth before or during deserialization of RPC request bodies (e.g., use a depth-limited JSON parser/pre-scan, or a custom `Deserializer` wrapper that rejects nesting beyond a safe bound, similar to `try_from_slice_with_limit` used for borsh peer payloads) [7](#0-6) .
- Alternatively/additionally, run untrusted JSON parsing on a dedicated thread with a generously sized stack, or convert the recursive parts of the request-processing pipeline (particularly anything that recurses into nested `Value`/`Batch`) to bounded-depth or iterative processing.
- Add a regression test analogous to `test_invalid_methods` that POSTs a deeply nested JSON body and asserts a clean `400 Bad Request` rather than a process crash [8](#0-7) .

### Proof of Concept
Send a single unauthenticated HTTP POST to the node's JSON-RPC port (`/`, default 3030) with a body such as:
```
{"jsonrpc":"2.0","id":1,"method":"query","params": <N nested arrays, e.g. "[".repeat(200000) + "]".repeat(200000)>}
```
or equivalently a deeply nested JSON-RPC `Batch` (`[[[[[ ... ]]]]]` at the top level, which is valid input to the untagged `Message` enum). This payload is well under the documented 10MB body limit but forces `serde_json`/`Message`/`Value` deserialization to recurse hundreds of thousands of levels deep in `rpc_handler()` → `JsonRpcHandler::process()` → `near_jsonrpc_primitives::message::from_slice`, exhausting the call stack before any application-level validation (method name lookup, params schema, etc.) is reached.

### Citations

**File:** chain/jsonrpc/src/lib.rs (L2928-2938)
```rust
async fn rpc_handler(
    State(handler): State<Arc<JsonRpcHandler>>,
    headers: axum::http::HeaderMap,
    Json(request): Json<Message>,
) -> Response {
    let source = if headers.contains_key(SHARDED_RPC_COORDINATOR_HEADER) {
        RequestSource::Coordinator
    } else {
        RequestSource::User
    };
    let message = handler.process(request.clone(), source).await;
```

**File:** chain/jsonrpc-primitives/src/message.rs (L158-181)
```rust
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(untagged)]
pub enum Message {
    /// An RPC request.
    Request(Request),
    /// A response to a Request.
    Response(Response),
    /// A notification.
    Notification(Notification),
    /// A batch of more requests or responses.
    ///
    /// The protocol allows bundling multiple requests, notifications or responses to a single
    /// message.
    ///
    /// This variant has no direct constructor and is expected to be constructed manually.
    Batch(Vec<Message>),
    /// An unmatched sub entry in a `Batch`.
    ///
    /// When there's a `Batch` and an element doesn't conform to the JSONRPC 2.0 format, that one
    /// is represented by this. This is never produced as a top-level value when parsing, the
    /// `Err(Broken::Unmatched)` is used instead. It is not possible to serialize.
    #[serde(skip_serializing)]
    UnmatchedSub(Value),
}
```

**File:** chain/jsonrpc-primitives/src/message.rs (L262-271)
```rust
pub fn from_slice(s: &[u8]) -> Parsed {
    decoded_to_parsed(::serde_json::de::from_slice(s))
}

/// Read a [Message](enum.Message.html) from a string.
///
/// Invalid JSON or JSONRPC messages are reported as [Broken](enum.Broken.html).
pub fn from_str(s: &str) -> Parsed {
    from_slice(s.as_bytes())
}
```

**File:** chain/jsonrpc/RPC_ARCHITECTURE.md (L95-95)
```markdown
Middleware: CORS (configurable via `cors_allowed_origins`) and request body size limit (default 10MB).
```

**File:** core/primitives/src/action/delegate.rs (L433-443)
```rust
    impl borsh::de::BorshDeserialize for NonDelegateAction {
        fn deserialize_reader<R: Read>(rd: &mut R) -> ::core::result::Result<Self, Error> {
            match u8::deserialize_reader(rd)? {
                n if DELEGATE_VARIANT_NUMBERS.contains(&n) => Err(Error::new(
                    ErrorKind::InvalidInput,
                    "DelegateAction mustn't contain a nested one",
                )),
                n => borsh::de::EnumExt::deserialize_variant(rd, n).map(Self),
            }
        }
    }
```

**File:** core/store/src/trie/trie_recording.rs (L302-304)
```rust
        // Non recursive approach to avoid any potential stack overflows.
        let mut queue: VecDeque<CryptoHash> = VecDeque::new();
        queue.push_back(*subtree_root);
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

**File:** chain/jsonrpc/jsonrpc-tests/tests/rpc_query.rs (L577-618)
```rust
#[tokio::test]
async fn test_invalid_methods() {
    let setup = create_test_setup_with_node_type(NodeType::NonValidator);
    let client = new_client(&setup.server_addr);

    let method_names = vec![
        serde_json::json!("\u{0}\u{0}\u{0}k\u{0}\u{0}\u{0}\u{0}\u{0}\u{0}\u{0}\u{0}\u{0}\u{0}SRP"),
        serde_json::json!(null),
        serde_json::json!(true),
        serde_json::json!(false),
        serde_json::json!(0),
        serde_json::json!(""),
    ];

    for method_name in method_names {
        let json = serde_json::json!({
            "jsonrpc": "2.0",
            "id": "dontcare",
            "method": &method_name,
            "params": serde_json::json!([]),
        });
        let (status, response_bytes) = client
            .transport
            .send_http_request(
                "/",
                json.to_string().as_bytes().to_vec(),
                JSONRPC_RESPONSE_LIMIT,
                &[],
            )
            .await
            .unwrap();

        assert_eq!(status, StatusCode::BAD_REQUEST);

        let response: serde_json::Value = serde_json::from_slice(&response_bytes).unwrap();
        assert!(
            response["error"] != serde_json::json!(null),
            "Invalid method {:?} must return error",
            method_name
        );
    }
}
```
