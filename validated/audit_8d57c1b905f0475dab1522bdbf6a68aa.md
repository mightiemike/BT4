### Title
Declare allow-list bypass via unauthenticated `sender_address` enables unauthorized Sierra→CASM compilation and permanent class storage - (File: crates/apollo_gateway/src/gateway.rs)

### Summary
The gateway's `authorized_declarer_accounts` permission gate (`check_declare_permissions`) is evaluated against the raw, unauthenticated `sender_address` field of an incoming `RpcDeclareTransaction`, before any signature/ownership verification of that address occurs. The privileged side effect the gate is meant to guard — permanent compilation and storage of the declared Sierra class in the Class Manager — is triggered immediately afterward, while true authorization (the account's `__validate_declare__` signature check) is only performed later, in stateful validation. An unprivileged caller can therefore set `sender_address` to any allow-listed address (without controlling its keys) to pass the gate and force class compilation/storage, even though the transaction is guaranteed to fail stateful validation afterwards.

### Finding Description
In `add_tx_inner`, the order of operations is:
1. `check_declare_permissions(declare_tx)` — checks `self.config.is_authorized_declarer(&declare_v3_tx.sender_address)` [1](#0-0) , implemented as a simple allow-list membership check on the address field supplied by the caller [2](#0-1) .
2. `stateless_tx_validator.validate(&tx)` — performs only stateless/format checks (size limits, signature length, calldata length, Sierra version bounds), not signature verification against `sender_address` [3](#0-2) .
3. `convert_rpc_tx_to_internal_and_executable_txs` calls `TransactionConverter::convert_rpc_tx_to_internal`, which for `Declare` transactions immediately calls `self.class_manager_client.add_class(tx.contract_class)` [4](#0-3) .
4. Only afterward does `stateful_transaction_validator.extract_state_nonce_and_run_validations` run the actual `__validate_declare__` entry point that verifies the caller controls the account at `sender_address` [5](#0-4) .

`ClassManager::add_class` compiles the Sierra class (CPU-heavy Sierra→CASM compilation) and, on success, permanently persists it via `CachedClassStorage::set_class` → `FsClassStorage::set_class`, which atomically writes the class to disk and marks it as existent [6](#0-5) [7](#0-6) . This write happens unconditionally as soon as `add_class` is called — it is not rolled back if the surrounding declare transaction subsequently fails stateful validation (signature check failure, insufficient fee, invalid nonce, etc.).

Because `check_declare_permissions` authenticates nothing about `sender_address` — it is merely a caller-supplied struct field in the unsigned-at-that-point RPC payload — any unprivileged sender can impersonate an authorized address for the purpose of passing this specific gate, triggering the compilation/storage side effect that the allow-list was designed to prevent, before the mismatch is ever detected.

### Impact Explanation
The `authorized_declarer_accounts` config exists specifically to restrict who may trigger Sierra→CASM compilation and class storage (a CPU- and disk-consuming, permissioned action) [8](#0-7) . This control is completely bypassable: any unprivileged party can force the sequencer to compile and permanently persist an arbitrary contract class by simply spoofing the `sender_address` field to match an allow-listed account, without owning that account's keys. This defeats the security purpose of the declarer allow-list feature entirely — turning a permissioned "declare" gate into a no-op check against user-controlled data — while the true declare transaction is guaranteed to be rejected downstream in stateful validation. The stored class also persists in the Class Manager for future reuse (idempotent by content hash), meaning even after rejection, the compiled class remains available.

### Likelihood Explanation
Trivially reachable by any external, unauthenticated caller via a single crafted `RpcTransaction::Declare` request to the gateway's `add_tx` — no special privileges, valid signature, or account ownership of the spoofed address is required to pass `check_declare_permissions`.

### Recommendation
Move the `authorized_declarer_accounts` authorization check to occur only after the account's ownership of `sender_address` has been cryptographically verified (i.e., after `__validate_declare__` succeeds in stateful validation), or otherwise ensure `add_class`/compilation is not invoked until the sender's identity has been authenticated. Alternatively, bind the allow-list check to a value that cannot be spoofed prior to signature verification.

### Proof of Concept
1. Configure the gateway with `authorized_declarer_accounts = [0xAUTH]`, where `0xAUTH` is an account the attacker does not control.
2. As an unprivileged attacker, submit an `RpcTransaction::Declare(V3)` with `sender_address = 0xAUTH`, an arbitrary attacker-chosen Sierra class, and an invalid/garbage signature.
3. `check_declare_permissions` passes because `sender_address` matches the allow-list [9](#0-8) .
4. `stateless_tx_validator.validate` passes (only checks format/sizes).
5. `convert_rpc_tx_to_internal` calls `class_manager_client.add_class(contract_class)`, compiling and permanently storing the attacker's class under its class hash [4](#0-3) .
6. Stateful validation subsequently fails (invalid signature for `0xAUTH`), and the gateway returns an error to the attacker — but the compiled class remains stored in the Class Manager, and the compilation resources were already spent, despite the attacker never being an authorized declarer.

Note: I was unable to fully confirm within the available searches whether any additional guard (e.g., signature pre-check) exists elsewhere in the stateless validator that I did not locate; the `stateless_transaction_validator.rs` full contents were not retrievable in this session due to a tool error, though the config struct fields inspected (`StatelessTransactionValidatorConfig`) show only format/size-related fields with no signature-verification field, supporting the conclusion above.

### Citations

**File:** crates/apollo_gateway/src/gateway.rs (L228-233)
```rust
        if let RpcTransaction::Declare(ref declare_tx) = tx {
            if let Err(e) = self.check_declare_permissions(declare_tx) {
                metric_counters.record_add_tx_failure(&e);
                return Err(e);
            }
        }
```

**File:** crates/apollo_gateway/src/gateway.rs (L253-266)
```rust
        let (internal_tx, executable_tx, proof_data) =
            self.convert_rpc_tx_to_internal_and_executable_txs(tx, &tx_signature).await?;
        drop(compilation_permit);

        let mut stateful_transaction_validator = self
            .stateful_tx_validator_factory
            .instantiate_validator(self.config.dynamic_config.native_classes_whitelist.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;

        let nonce = stateful_transaction_validator
            .extract_state_nonce_and_run_validations(&executable_tx, self.mempool_client.clone())
            .await
            .inspect_err(|e| metric_counters.record_add_tx_failure(e))?;
```

**File:** crates/apollo_gateway/src/gateway.rs (L420-431)
```rust
        let RpcDeclareTransaction::V3(declare_v3_tx) = declare_tx;
        if !self.config.is_authorized_declarer(&declare_v3_tx.sender_address) {
            return Err(StarknetError {
                code: StarknetErrorCode::KnownErrorCode(
                    KnownStarknetErrorCode::UnauthorizedDeclare,
                ),
                message: format!(
                    "Account address {} is not allowed to declare contracts.",
                    &declare_v3_tx.sender_address
                ),
            });
        }
```

**File:** crates/apollo_gateway_config/src/config.rs (L99-106)
```rust
        dump.extend(ser_optional_param(
            &serialize_optional_comma_separated(&self.authorized_declarer_accounts),
            "".to_string(),
            "authorized_declarer_accounts",
            "Authorized declarer accounts. If set, only these accounts can declare new contracts. \
             Addresses are in hex format and separated by a comma with no space.",
            ParamPrivacyInput::Public,
        ));
```

**File:** crates/apollo_gateway_config/src/config.rs (L140-147)
```rust
impl GatewayConfig {
    pub fn is_authorized_declarer(&self, declarer_address: &ContractAddress) -> bool {
        match &self.static_config.authorized_declarer_accounts {
            Some(allowed_accounts) => allowed_accounts.contains(declarer_address),
            None => true,
        }
    }
}
```

**File:** crates/apollo_gateway_config/src/config.rs (L166-186)
```rust
#[derive(Clone, Debug, Deserialize, PartialEq, Serialize, Validate)]
pub struct StatelessTransactionValidatorConfig {
    // If true, ensures that at least one resource bound (L1, L2, or L1 data) is greater than zero.
    pub validate_resource_bounds: bool,
    // TODO(AlonH): Remove the `min_gas_price` field from this struct and use the one from the
    // versioned constants.
    pub min_gas_price: u128,
    pub max_l2_gas_amount: u64,
    pub max_calldata_length: usize,
    pub max_signature_length: usize,
    pub max_proof_size: usize,

    // Declare txs specific config.
    pub max_contract_bytecode_size: usize,
    pub max_contract_class_object_size: usize,
    pub min_sierra_version: VersionId,
    pub max_sierra_version: VersionId,

    // If true, allows transactions with non-empty proof_facts or proof fields.
    pub allow_client_side_proving: bool,
}
```

**File:** crates/apollo_transaction_converter/src/transaction_converter.rs (L347-360)
```rust
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => {
                let ClassHashes { class_hash, executable_class_hash_v2 } =
                // TODO(Dori): Make this async and spawn a task to compile and add it to the class manager.
                    self.class_manager_client.add_class(tx.contract_class).await?;
                // TODO(Aviv): Ensure that we do not want to
                // allow declare with compiled class hash v1.
                if tx.compiled_class_hash != executable_class_hash_v2 {
                    return Err(TransactionConverterError::ValidateCompiledClassHashError(
                        ValidateCompiledClassHashError::CompiledClassHashMismatch {
                            computed_class_hash: executable_class_hash_v2,
                            supplied_class_hash: tx.compiled_class_hash,
                        },
                    ));
                }
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L70-113)
```rust
    #[instrument(skip(self, class), ret, err)]
    pub async fn add_class(&mut self, class: RawClass) -> ClassManagerResult<ClassHashes> {
        let sierra_class = SierraContractClass::try_from(&class)?;
        let class_hash = sierra_class.calculate_class_hash();
        if let Ok(Some(executable_class_hash_v2)) =
            self.classes.get_executable_class_hash_v2(class_hash)
        {
            // Class already exists.
            return Ok(ClassHashes { class_hash, executable_class_hash_v2 });
        }

        let compilation_start_time = Instant::now();
        let (raw_executable_class, executable_class_hash_v2) =
            self.compiler.compile(class.clone()).await.map_err(|err| match err {
                SierraCompilerClientError::SierraCompilerError(error) => {
                    ClassManagerError::SierraCompiler { class_hash, error }
                }
                SierraCompilerClientError::ClientError(error) => {
                    ClassManagerError::Client(error.to_string())
                }
            })?;
        debug!(
            %class_hash,
            compiled_class_hash = %executable_class_hash_v2,
            compilation_elapsed_ms = compilation_start_time.elapsed().as_millis(),
            class_size_bytes =
                class.size().map_or("Failed to get class size".to_owned(), |size| size.to_string()),
            casm_size_bytes =
                raw_executable_class.size().map_or("Failed to get casm size".to_owned(), |size| size.to_string()),
            "Finished compiling class."
        );

        self.validate_class_length(&raw_executable_class)?;
        Self::validate_class_version(&sierra_class)?;
        self.classes.set_class(
            class_hash,
            class,
            executable_class_hash_v2,
            raw_executable_class,
        )?;

        let class_hashes = ClassHashes { class_hash, executable_class_hash_v2 };
        Ok(class_hashes)
    }
```

**File:** crates/apollo_class_manager/src/class_storage.rs (L503-522)
```rust
impl ClassStorage for FsClassStorage {
    type Error = FsClassStorageError;

    #[instrument(skip(self, class, executable_class), level = "debug", ret, err)]
    fn set_class(
        &mut self,
        class_id: ClassId,
        class: RawClass,
        executable_class_hash_v2: ExecutableClassHash,
        executable_class: RawExecutableClass,
    ) -> Result<(), Self::Error> {
        if self.contains_class(class_id)? {
            return Ok(());
        }

        self.write_class_atomically(class_id, class, executable_class)?;
        self.mark_class_id_as_existent(class_id, executable_class_hash_v2)?;

        Ok(())
    }
```
