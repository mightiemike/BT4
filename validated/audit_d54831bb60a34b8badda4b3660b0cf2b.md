I have traced the full code path. Here is my analysis.

**Path trace:**

1. `TryFrom<protobuf::Class> for (ApiContractClass, ClassHash)` — decodes the class body and extracts `class_hash` directly from the protobuf field, with **no verification** that `hash(body) == class_hash`. [1](#0-0) 

2. `ClassStreamBuilder::parse_data_for_block` — checks only that the received `class_hash` is present in the stored state diff (`is_declared` check). It does **not** verify that the class body hashes to the claimed `class_hash`. [2](#0-1) 

3. `write_to_storage` dispatches differently by class type:

   - **Cairo 1 (Sierra):** calls `class_manager_client.add_class(class)`. Inside `ClassManager::add_class`, the hash is **recomputed** from the body (`sierra_class.calculate_class_hash()`), and the class is stored under that computed hash — not the attacker-supplied one. The returned hash is currently ignored (there is an explicit `TODO` noting this), but the body is stored under `hash(B')`, not under H. The attack as described does not succeed for Cairo 1. [3](#0-2) 

   - **Cairo 0 (deprecated):** calls `class_manager_client.add_deprecated_class(class_hash, deprecated_class)`. Inside `ClassManager::add_deprecated_class`, the body is stored directly under the caller-supplied `class_id` with **no hash recomputation and no verification**. [4](#0-3) [5](#0-4) 

**Conclusion for the deprecated class path:**

A malicious P2P peer can send a `protobuf::Class` with `class_hash = H` (a hash legitimately present in the state diff's `deprecated_declared_classes`) and a class body `B'` that does not hash to H. The `parse_data_for_block` check only validates that H is in the state diff. The `add_deprecated_class` call then stores `B'` under `H` in the class manager storage without any hash-body consistency check. When a contract deployed at class hash H is subsequently called, the execution engine retrieves `B'` and executes the wrong code.

**Conclusion for the Cairo 1 path:**

The `ClassManager::add_class` recomputes the hash from the body, so `B'` is stored under `hash(B')`, not under H. The attack as described does not produce body-under-wrong-hash for Sierra classes (though the missing return-value check is a separate acknowledged TODO).

---

### Title
Deprecated (Cairo 0) class body stored under attacker-supplied hash without verification in P2P SQMR class-sync path — (`crates/apollo_p2p_sync/src/client/class.rs`)

### Summary
A malicious P2P peer can supply a `protobuf::Class` message carrying a deprecated (Cairo 0) class body `B'` paired with a class hash `H` that legitimately appears in the committed state diff. Neither the protobuf converter, `ClassStreamBuilder::parse_data_for_block`, nor `ClassManager::add_deprecated_class` verifies that `hash(B') == H`. The body `B'` is stored in the class manager under `H`, causing the wrong executable code to be selected whenever a contract at class hash `H` is invoked.

### Finding Description
`TryFrom<protobuf::Class>` blindly trusts the `class_hash` field from the wire message. [6](#0-5) 

`parse_data_for_block` validates only set-membership of the hash in the state diff, not body integrity. [2](#0-1) 

`ClassManager::add_deprecated_class` calls `set_deprecated_class(class_id, class)` directly, with no hash recomputation. [4](#0-3) 

Contrast with the Cairo 1 path, where `add_class` recomputes the hash from the body before storage. [7](#0-6) 

### Impact Explanation
After the attack, `get_executable(H)` returns `B'`. Any contract deployed at class hash `H` executes the attacker-chosen bytecode. This satisfies the **Critical** impact criterion: *Wrong compiled class / contract code selected for execution*.

### Likelihood Explanation
Any node that participates in P2P sync and connects to a malicious peer is exposed. No operator privilege is required; the attacker only needs to serve a well-formed protobuf response on the class-sync SQMR channel. The state diff containing `H` must already be committed (the class sync waits for the state diff marker), but that is a normal precondition, not a privilege barrier.

### Recommendation
In `ClassManager::add_deprecated_class`, compute the Pedersen/Poseidon class hash of the supplied body and assert it equals `class_id` before calling `set_deprecated_class`. Alternatively, perform this check in `write_to_storage` immediately after `add_deprecated_class` returns, analogous to the existing `TODO` comment for the Cairo 1 path. [8](#0-7) 

### Proof of Concept
1. Obtain any two distinct deprecated class bodies `B` and `B'` where `H = hash(B)` and `hash(B') ≠ H`.
2. Construct a `protobuf::Class { class_hash: H, class: Cairo0(B') }`.
3. Feed it through `ClassStreamBuilder::parse_data_for_block` with a `StorageReader` whose state diff contains `H` in `deprecated_declared_classes`.
4. Observe that `parse_data_for_block` returns `Ok(Some(...))` without error.
5. Call `write_to_storage` with a real `ClassManager`; observe that `get_deprecated_class(H)` returns `B'`, not `B`.

### Citations

**File:** crates/apollo_protobuf/src/converters/class.rs (L59-78)
```rust
impl TryFrom<protobuf::Class> for (ApiContractClass, ClassHash) {
    type Error = ProtobufConversionError;
    fn try_from(value: protobuf::Class) -> Result<Self, Self::Error> {
        let class = match value.class {
            Some(protobuf::class::Class::Cairo0(class)) => {
                ApiContractClass::DeprecatedContractClass(
                    deprecated_contract_class::ContractClass::try_from(class)?,
                )
            }
            Some(protobuf::class::Class::Cairo1(class)) => {
                ApiContractClass::ContractClass(state::SierraContractClass::try_from(class)?)
            }
            None => {
                return Err(missing("Class::class"));
            }
        };
        let class_hash =
            value.class_hash.ok_or(missing("Class::class_hash"))?.try_into().map(ClassHash)?;
        Ok((class, class_hash))
    }
```

**File:** crates/apollo_p2p_sync/src/client/class.rs (L38-39)
```rust
                // TODO(shahak): Test this flow.
                // TODO(shahak): Verify class hash matches class manager response. report if not.
```

**File:** crates/apollo_p2p_sync/src/client/class.rs (L51-65)
```rust
            for (class_hash, deprecated_class) in self.1 {
                // TODO(shahak): Test this flow.
                // TODO(shahak): Try to avoid cloning. See if ClientError can contain the request.
                while let Err(err) = class_manager_client
                    .add_deprecated_class(class_hash, deprecated_class.clone())
                    .await
                {
                    warn!(
                        "Failed writing deprecated class with hash {class_hash:?} to class \
                         manager. Trying again. Error: {err:?}"
                    );
                    trace!("Class: {deprecated_class:?}");
                    // TODO(shahak): Consider sleeping here.
                }
            }
```

**File:** crates/apollo_p2p_sync/src/client/class.rs (L131-148)
```rust
                let (is_declared, duplicate_class) = match api_contract_class {
                    ApiContractClass::ContractClass(contract_class) => (
                        declared_classes.get(&class_hash).is_some(),
                        declared_classes_result.insert(class_hash, contract_class).is_some(),
                    ),
                    ApiContractClass::DeprecatedContractClass(deprecated_contract_class) => (
                        deprecated_declared_classes.contains(&class_hash),
                        deprecated_declared_classes_result
                            .insert(class_hash, deprecated_contract_class)
                            .is_some(),
                    ),
                };

                if !is_declared {
                    return Err(ParseDataError::BadPeer(BadPeerError::ClassNotInStateDiff {
                        class_hash,
                    }));
                }
```

**File:** crates/apollo_class_manager/src/class_manager.rs (L71-113)
```rust
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

**File:** crates/apollo_class_manager/src/class_manager.rs (L136-144)
```rust
    #[instrument(skip(self, class), ret, err)]
    pub fn add_deprecated_class(
        &mut self,
        class_id: ClassId,
        class: RawExecutableClass,
    ) -> ClassManagerResult<()> {
        self.classes.set_deprecated_class(class_id, class)?;
        Ok(())
    }
```
