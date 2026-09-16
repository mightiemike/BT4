### Title
Internal error details from `StatefulValidatorError` (StateError/TransactionExecutorError) leaked to unprivileged tx senders via `ValidateFailure` error message - ([File: crates/apollo_gateway/src/stateful_transaction_validator.rs])

### Summary
`run_validate_entry_point` maps **any** error returned by `blockifier_validator.validate(account_tx)` — whose type is `StatefulValidatorError`, an enum that transparently wraps `StateError`, `TransactionExecutorError`, `TransactionExecutionError`, and `TransactionPreValidationError` — into a client-visible `StarknetError` using `e.to_string()` as the message, tagged with the "expected" `KnownStarknetErrorCode::ValidateFailure` code. [1](#0-0) 

This is inconsistent with the rest of the gateway's error-handling convention, which deliberately distinguishes "internal" failures (state read errors, storage errors, join errors) from expected client-input validation failures, and redacts the former behind a generic `"Internal error"` message logged only server-side via `StarknetError::internal_with_logging` / `internal_error_code()`. [2](#0-1) 

### Finding Description
`StatefulValidatorError` is defined as a thin, transparent wrapper enum: [3](#0-2) 

It can surface a `StateError` (e.g. read/storage-layer failures, deserialization errors, inconsistency errors from the state reader) or a `TransactionExecutorError` (block-state access/internal executor errors), not just legitimate `__validate__`/pre-validation rejections. Elsewhere in the same crate, the gateway is careful to route this class of "unexpected/internal" error into a sanitized `"Internal error"` response while logging the real error server-side (see `StarknetError::internal_with_logging`, used e.g. for class-manager/proof-manager/mempool client errors in `crates/apollo_gateway/src/errors.rs`, and for the join error one line above in the same function).

However, in `run_validate_entry_point`, the `.map_err` on the inner `validate()` result unconditionally forwards `e.to_string()` as the response `message`, without checking whether `e` is a genuine validation failure (e.g. `TransactionPreValidationError`) versus an internal/backend failure (`StateError`, `TransactionExecutorError`): [4](#0-3) 

This `StarknetError` then flows to the HTTP layer, which only performs light XSS/character sanitization (quote/whitespace substitution) and otherwise passes the message through verbatim to the client: [5](#0-4) 

Any unprivileged transaction sender can trigger a `StateError`/`TransactionExecutorError` path during stateful validation (for example by crafting a transaction that causes a state-read inconsistency, a contract-class lookup failure, or a `BLOCK_STATE_ACCESS_ERR`-style internal state condition) and receive the raw `Display` output of that internal error in the HTTP response, rather than the generic internal-error message the rest of the codebase deliberately uses for this error class.

### Impact Explanation
This is an information-leakage (CWE-209) issue: it can reveal internal implementation details of the sequencer's state/storage layer (error text from `StateError`/`TransactionExecutorError` variants, which may include internal identifiers, inconsistency descriptions, or backend-specific error text) to any external, unauthenticated transaction submitter. While this does not directly cause loss of funds or state-root divergence, it weakens defense-in-depth by giving attackers diagnostic information about internal failures useful for crafting further attacks against gateway/state-reader internals, and it is a clear deviation from the project's own established pattern of not exposing internal error text to clients.

### Likelihood Explanation
Reaching this code path only requires submitting a transaction through the public gateway that causes the blockifier's `perform_validations`/`execute` call to return a `StateError` or `TransactionExecutorError` instead of a normal validation rejection — reachable by any unprivileged transaction sender, with no special privileges required. The exact conditions needed to force a `StateError` (as opposed to ordinary validation failure) depend on state-reader implementation details not fully explored here (I could not fully enumerate which `StateError` variants are practically triggerable purely from an external gateway request without deeper access to `StateReaderAndContractManager`/`GatewayStateReaderWithCompiledClasses` behavior), so likelihood of triggering the internal-error variants specifically is uncertain and would need further investigation/testing.

### Recommendation
In `run_validate_entry_point`, distinguish `StatefulValidatorError::StateError` / `StatefulValidatorError::TransactionExecutorError` (internal/unexpected errors) from `StatefulValidatorError::TransactionPreValidationError` / `TransactionExecutionError` (expected validation failures). Route the former through `StarknetError::internal_with_logging` (generic `"Internal error"` message, full detail logged server-side only) and only the latter through the `KnownStarknetErrorCode::ValidateFailure` path with `e.to_string()`, consistent with how the rest of `crates/apollo_gateway/src/errors.rs` handles internal vs. user-facing errors.

### Proof of Concept
1. Submit a transaction via the gateway `add_tx` HTTP endpoint that passes stateless validation but causes the underlying state reader to hit an internal/state error during stateful validation (e.g., a condition that triggers `StateError` inside `perform_validations`/`execute` in `crates/blockifier/src/blockifier/stateful_validator.rs`).
2. Observe that `run_validate_entry_point` converts this into a `StarknetError` with `code: ValidateFailure` and `message: e.to_string()` [6](#0-5) .
3. Observe the HTTP server forwards this message to the client after only light character sanitization [7](#0-6) , exposing internal error text that, per the codebase's own convention (`StarknetError::internal_with_logging`), should instead have been logged server-side and replaced with a generic `"Internal error"` message.

### Citations

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L336-354)
```rust
        #[allow(clippy::result_large_err)]
        tokio::task::spawn_blocking(move || {
            cur_span.in_scope(|| {
                let state = CachedState::new(state_reader_and_contract_manager);
                let mut blockifier_validator = StatefulValidator::create(state, block_context);
                blockifier_validator.validate(account_tx)
            })
        })
        .await
        .map_err(|e| StarknetError {
            code: StarknetErrorCode::UnknownErrorCode(
                "StarknetErrorCode.InternalError".to_string(),
            ),
            message: format!("Blocking task join error when running the validate entry point: {e}"),
        })?
        .map_err(|e| StarknetError {
            code: StarknetErrorCode::KnownErrorCode(KnownStarknetErrorCode::ValidateFailure),
            message: e.to_string(),
        })?;
```

**File:** crates/apollo_gateway_types/src/deprecated_gateway_error.rs (L75-92)
```rust
impl StarknetError {
    pub fn internal_with_logging(log_message: &str, err: impl std::error::Error) -> Self {
        error!("Internal error: {log_message}: {err}.");
        Self { code: Self::internal_error_code(), message: "Internal error".to_string() }
    }

    pub fn internal_with_signature_logging(
        log_message: impl Display,
        tx_signature: &TransactionSignature,
        err: impl std::error::Error,
    ) -> Self {
        let log_message = format!("{log_message}: Transaction signature: {tx_signature:?}");
        Self::internal_with_logging(&log_message, err)
    }

    pub fn is_internal(&self) -> bool {
        self.code == Self::internal_error_code()
    }
```

**File:** crates/blockifier/src/blockifier/stateful_validator.rs (L30-40)
```rust
#[derive(Debug, Error)]
pub enum StatefulValidatorError {
    #[error(transparent)]
    StateError(#[from] StateError),
    #[error(transparent)]
    TransactionExecutionError(#[from] TransactionExecutionError),
    #[error(transparent)]
    TransactionExecutorError(#[from] TransactionExecutorError),
    #[error(transparent)]
    TransactionPreValidationError(#[from] TransactionPreValidationError),
}
```

**File:** crates/apollo_http_server/src/errors.rs (L95-113)
```rust
fn gw_client_err_into_response(err: GatewayClientError) -> Response {
    let (response_code, deprecated_gateway_error) = match err {
        GatewayClientError::ClientError(e) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            StarknetError::internal_with_logging("Failed to process client request", e),
        ),
        GatewayClientError::GatewayError(GatewayError::DeprecatedGatewayError {
            source,
            p2p_message_metadata: _,
        }) => {
            // TODO(yair): Find out what is the p2p_message_metadata and whether it needs to be
            // added to the error response.
            (StatusCode::BAD_REQUEST, source)
        }
    };

    let response_body = serialize_error(&deprecated_gateway_error);

    (response_code, response_body).into_response()
```

**File:** crates/apollo_http_server/src/errors.rs (L125-139)
```rust
/// Serializes a `StarknetError` into an HTTP response, encode the error message
/// to defend potential Cross-Site risks.
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
