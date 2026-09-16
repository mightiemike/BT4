Based on my investigation, the strongest analog to the ion-java `StackOverflowError` bug class in this codebase is the JSON deserialization path in the HTTP gateway entrypoint, which parses an unbounded-nesting-depth `serde_json::Value`/typed structure from an untrusted, unprivileged transaction sender before any depth limiting is applied.

### Title
Unbounded recursive JSON deserialization of untrusted transaction/contract-class payloads can cause a `StackOverflowError`-equivalent process crash (gateway DoS) - ([File: crates/apollo_http_server/src/http_server.rs])

### Summary
### Finding Description
The HTTP gateway's `add_rpc_tx` and `add_tx` handlers deserialize the raw, attacker-controlled request body directly into Rust structures via `serde_json` (`Json<RpcTransaction>` extraction and `serde_json::from_str(&tx)` into `DeprecatedGatewayTransactionV3`), and separately, declared contract classes are wrapped as raw `serde_json::Value` trees (`SerializedClass<T>(Arc<serde_json::Value>, ...)`) that get parsed via `serde_json::from_reader`/`from_value` in `RawClass`/`RawExecutableClass` conversions. [1](#0-0) [2](#0-1) 

`serde_json`'s recursive-descent deserializer (used both for typed structs with nested/optional/enum fields and for the generic `serde_json::Value` tree) recurses once per nesting level of the input JSON with no built-in depth limit. This is the exact bug class described in the ion-java advisory (CWE-770): deeply nested untrusted data drives an unbounded call stack during deserialization, producing a stack overflow before any size/structural validation (`validate_class_length`, `validate_sierra_version`, etc., in `StatelessTransactionValidator`) has a chance to run. [3](#0-2) 

The gateway does bound the *compressed and decompressed byte size* of the request body (`RequestBodyLimitLayer`, `DefaultBodyLimit::max`), but byte-size limits do not bound JSON *nesting depth* — a small payload (e.g., a few KB) can contain hundreds of thousands of nested array/object brackets (`[[[[...]]]]`), which is exactly the ion-java-class attack vector. [4](#0-3) 

### Impact Explanation
A single unprivileged network client (no special permissions, not even a signed transaction — this can be hit before signature/fee validation, since parsing happens before `StatelessTransactionValidator::validate`) can crash the gateway process by sending a deeply nested JSON body. This is a process-level `SIGSEGV`/abort (Rust stack overflow), not a catchable `Result` error — it does not trigger the existing "revert"/"out of gas" mitigations that protect the Cairo-execution recursion paths (`max_recursion_depth`, `RUST_MIN_STACK`, thread-pool `stack_size` for cairo-native calls, all of which are irrelevant here since this is plain Rust code, not Cairo/Sierra execution). Repeated requests against multiple/most sequencer gateway replicas can degrade network availability ("a network unable to confirm new transactions"), matching the CVSS `AV:N/AC:L/PR:N/UI:N/.../A:H` profile of the referenced advisory.

### Likelihood Explanation
High reachability: the vulnerable parsing occurs on the very first hop of transaction ingestion (`add_rpc_tx`/`add_tx` handlers), reachable by any network peer able to POST to the gateway HTTP endpoint, with no authentication, no prior validation, and no fee payment required to trigger the crash.

### Recommendation
Impose an explicit maximum JSON nesting depth check (or use a depth-limited JSON parser / recursion-limit guard) before or during deserialization of untrusted request bodies in `crates/apollo_http_server/src/http_server.rs` (`add_rpc_tx`, `add_tx`) and in `SerializedClass`/`RawClass` JSON parsing in `crates/apollo_compile_to_casm_types/src/lib.rs`. `serde_json` supports iterative/streaming parsing with depth tracking that can reject inputs exceeding a configured depth before recursing further.

### Proof of Concept
I could not execute this against a running instance (no filesystem/terminal access in this session), but the PoC is: send a POST to `/gateway/add_rpc_transaction` with a body such as `{"type":"INVOKE","calldata":` + `[` × N + `]` × N + `,...}` (or equivalently deeply nested inside any JSON array/object field of `RpcTransaction`), with N in the hundreds of thousands — small in byte size (well under `max_request_body_size`) but deep enough to exceed the thread stack during `serde_json`'s recursive parse in the `Json<RpcTransaction>` extractor.

**Caveat / uncertainty**: I was not able to run this PoC or inspect `serde_json`'s exact recursion behavior/version-specific mitigations in this dependency (e.g., some `serde_json` versions have partial recursion-limit protections for `Value` but not necessarily for `#[derive(Deserialize)]`-generated code paths). I could not find any depth-limiting code in the sequencer's own gateway/http-server crates guarding against this. This is a genuine gap I identified via static review of the ingestion path; confirming actual exploitability (i.e., that the default `serde_json` recursion does overflow the specific thread stack size used by the axum/tokio worker before hitting the request body size limit) would require running a live reproduction, which I recommend a Devin session perform if you want empirical confirmation.

### Citations

**File:** crates/apollo_http_server/src/http_server.rs (L126-163)
```rust
    pub fn app(&self) -> Router {
        Router::new()
            // Json Rpc endpoint
            .route(
                "/gateway/add_rpc_transaction",
                self.post_method_router(add_rpc_tx),
            )
            // Rest api endpoint
            .route(
                "/gateway/add_transaction",
                self.post_method_router(add_tx),
            )
            // TODO(shahak): Remove this once we fix the centralized simulator to not use is_alive
            // and is_ready.
            .route(
                "/gateway/is_alive",
                get(|| futures::future::ready("Gateway is alive".to_owned()))
            )
            .route("/gateway/is_ready", get(is_ready))
            .layer(Extension(self.app_state.clone()))
            // Hard streaming limit on decompressed bytes — wraps the body in
            // http_body_util::Limited which errors during poll_frame() once the
            // limit is exceeded, preventing zip bombs from expanding in memory.
            .layer(RequestBodyLimitLayer::new(self.config.static_config.max_request_body_size))
            .layer(RequestDecompressionLayer::new())
            // Cap compressed wire bytes to bound network I/O.
            .layer(RequestBodyLimitLayer::new(self.config.static_config.max_request_body_size))
    }

    fn post_method_router<H, T, S>(&self, handler: H) -> MethodRouter<S>
    where
        H: Handler<T, S> + Send + Sync + 'static,
        T: Send + 'static,
        S: Clone + Send + Sync + 'static,
    {
        post(handler).layer(DefaultBodyLimit::max(self.config.static_config.max_request_body_size))
    }
}
```

**File:** crates/apollo_http_server/src/http_server.rs (L167-217)
```rust
#[instrument(skip(app_state, tx))]
async fn add_rpc_tx(
    Extension(app_state): Extension<AppState>,
    headers: HeaderMap,
    Json(tx): Json<RpcTransaction>,
) -> HttpServerResult<Json<GatewayOutput>> {
    debug!("ADD_TX_START: Http server received a new transaction.");

    let HttpServerDynamicConfig { accept_new_txs, .. } = app_state.get_dynamic_config();
    check_new_transactions_are_allowed(accept_new_txs)?;

    ADDED_TRANSACTIONS_TOTAL.increment(1);
    set_unix_now_seconds(&LAST_RECEIVED_TRANSACTION_TIMESTAMP_SECONDS);
    add_tx_inner(app_state, headers, tx).await
}

#[instrument(skip(app_state, tx))]
#[sequencer_latency_histogram(HTTP_SERVER_ADD_TX_LATENCY, true)]
async fn add_tx(
    Extension(app_state): Extension<AppState>,
    headers: HeaderMap,
    tx: String,
) -> HttpServerResult<Json<GatewayOutput>> {
    debug!("ADD_TX_START: Http server received a new transaction.");

    let HttpServerDynamicConfig { accept_new_txs, max_sierra_program_size } =
        app_state.get_dynamic_config();
    check_new_transactions_are_allowed(accept_new_txs)?;

    ADDED_TRANSACTIONS_TOTAL.increment(1);
    set_unix_now_seconds(&LAST_RECEIVED_TRANSACTION_TIMESTAMP_SECONDS);
    let tx: DeprecatedGatewayTransactionV3 = match serde_json::from_str(&tx) {
        Ok(value) => value,
        Err(e) => {
            validate_supported_tx_version_str(&tx).inspect_err(|e| {
                debug!("Error while validating transaction version: {}", e);
                increment_failure_metrics(e);
            })?;

            debug!("Error while parsing transaction: {}", e);
            check_supported_resource_bounds_and_increment_metrics(&tx);
            return Err(e.into());
        }
    };

    let rpc_tx = tx.convert_to_rpc_tx(max_sierra_program_size).inspect_err(|e| {
        debug!("Error while converting deprecated gateway transaction into RPC transaction: {}", e);
    })?;

    add_tx_inner(app_state, headers, rpc_tx).await
}
```

**File:** crates/apollo_compile_to_casm_types/src/lib.rs (L82-111)
```rust
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct SerializedClass<T>(Arc<serde_json::Value>, std::marker::PhantomData<T>);

impl<T> SerializedClass<T> {
    pub fn into_value(self) -> serde_json::Value {
        Arc::unwrap_or_clone(self.0)
    }

    pub fn size(&self) -> RawClassResult<usize> {
        Ok(size_of_serialized(&self.0)?)
    }

    fn new(value: serde_json::Value) -> Self {
        Self(Arc::new(value), std::marker::PhantomData)
    }

    pub fn from_file(path: PathBuf) -> RawClassResult<Option<Self>> {
        let file = match File::open(path) {
            Ok(file) => file,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(e.into()),
        };

        match serde_json::from_reader(BufReader::new(file)) {
            Ok(value) => Ok(Some(Self::new(value))),
            // In case the file was deleted/tempered with until actual read is done.
            Err(e) if e.is_io() && e.to_string().contains("No such file or directory") => Ok(None),
            Err(e) => Err(e.into()),
        }
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L280-337)
```rust
    fn validate_declare_tx(
        &self,
        declare_tx: &RpcDeclareTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let contract_class = match declare_tx {
            RpcDeclareTransaction::V3(tx) => &tx.contract_class,
        };
        self.validate_sierra_version(&contract_class.sierra_program)?;
        self.validate_class_length(contract_class)?;
        self.validate_entry_points_sorted_and_unique(contract_class)?;
        Ok(())
    }

    fn validate_sierra_version(
        &self,
        sierra_program: &[Felt],
    ) -> StatelessTransactionValidatorResult<()> {
        // Any patch version is valid. (i.e. when check version for upper bound, we ignore the Z
        // part in a version X.Y.Z).
        let mut max_sierra_version = self.config.max_sierra_version;
        max_sierra_version.0.patch = usize::MAX;

        let sierra_version = VersionId::from_sierra_program(sierra_program)?;
        if self.config.min_sierra_version <= sierra_version && sierra_version <= max_sierra_version
        {
            return Ok(());
        }

        Err(StatelessTransactionValidatorError::UnsupportedSierraVersion {
            version: sierra_version,
            min_version: self.config.min_sierra_version,
            max_version: self.config.max_sierra_version,
        })
    }

    fn validate_class_length(
        &self,
        contract_class: &starknet_api::state::SierraContractClass,
    ) -> StatelessTransactionValidatorResult<()> {
        if contract_class.sierra_program.len() > self.config.max_contract_bytecode_size {
            return Err(StatelessTransactionValidatorError::ContractBytecodeSizeTooLarge {
                contract_bytecode_size: contract_class.sierra_program.len(),
                max_contract_bytecode_size: self.config.max_contract_bytecode_size,
            });
        }

        let contract_class_object_size = serde_json::to_string(&contract_class)
            .expect("Unexpected error serializing contract class.")
            .len();
        if contract_class_object_size > self.config.max_contract_class_object_size {
            return Err(StatelessTransactionValidatorError::ContractClassObjectSizeTooLarge {
                contract_class_object_size,
                max_contract_class_object_size: self.config.max_contract_class_object_size,
            });
        }

        Ok(())
    }
```
