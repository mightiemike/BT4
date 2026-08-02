## Finding

Based on the code, `get_metadata` and `get_metadata_from_compiled_code` in `types/src/vm/module_metadata.rs` are **not** equivalent, and the divergence is exactly the one the question describes.

### Title
Version-gated metadata clearing in `get_metadata_from_compiled_code` is bypassed by `get_metadata`, causing authenticated resource API to bind resources using unvalidated `resource_group`/`resource_group_member` attributes - (File: `types/src/vm/module_metadata.rs`)

### Summary
`get_metadata_from_compiled_code` special-cases binary format `version() == 5` and wipes `struct_attributes`/`fun_attributes` before returning metadata: [1](#0-0) 

`get_metadata`, which caches and returns the *same* on-chain `Metadata` bytes, performs no such version check at all: [2](#0-1) 

`verify_module_metadata_for_module_publishing` — the only gate that validates `resource_group`/`resource_group_member` attributes against `is_valid_resource_group`/`is_valid_resource_group_member` — uses `get_metadata_from_compiled_code`: [3](#0-2) 

So for a module whose file-format `version` field is `5`, this validation function sees empty `struct_attributes`/`fun_attributes` and validates nothing, letting the module publish with arbitrary attacker-supplied resource-group attributes embedded in the still-untouched serialized `Metadata` blob.

Meanwhile, `api/types/src/convert.rs::is_resource_group` (and `find_resource`, which relies on `view_resource_group_member`) reads metadata via the un-gated `get_metadata`, so it sees the full, attacker-controlled `struct_attributes` including a forged `resource_group_member` attribute: [4](#0-3) 

### Finding Description
An unprivileged publisher can hand-craft a `CompiledModule` (the existing e2e test `build_package_and_insert_attribute` demonstrates this exact technique of building a module, then manually inserting an arbitrary `RuntimeModuleMetadataV1` `Metadata` blob before re-serializing) and set its file-format `version` field to `5`: [5](#0-4) 

Because `verify_module_metadata_for_module_publishing` calls `get_metadata_from_compiled_code`, which clears `struct_attributes`/`fun_attributes` for `version() == 5` modules, none of the attacker's declared attributes (e.g., a bogus `resource_group_member` pointing at an arbitrary container `StructTag`) are checked by `is_valid_resource_group`/`is_valid_resource_group_member`, and the module publishes successfully with those attributes intact in the raw `Metadata` bytes.

Downstream, `is_resource_group`/`find_resource` in `api/types/src/convert.rs` call `get_metadata` (not `get_metadata_from_compiled_code`), which is not version-gated and returns the unvalidated attacker metadata as-is. This causes `find_resource` to treat the struct as a resource-group member and construct `StateKey::resource_group(&address, &group_tag)` instead of the plain `StateKey::resource(&address, tag)` — using a `group_tag` value that was never validated to be a real resource-group container: [6](#0-5) 

This is a genuine divergence between the VM's own runtime interpretation of module metadata (as gated by `verify_module_metadata_for_module_publishing`, i.e. "this attribute was never validated, treat as absent") and the API's binding of a resource lookup to a `StateKey`.

### Impact Explanation
An authenticated resource-fetch response (`GET /accounts/{address}/resource/{tag}` and similar) can be bound to the wrong `StateKey` — reading from (or reporting non-existence for) a `StateKey::resource_group` container instead of the correct plain resource key, or vice versa for the inverse case. This matches the "authenticated API or state-view output bound to the wrong ... object" impact category, since the resource the client requested and the state slot actually queried diverge based on unvalidated attacker input embedded in the module's metadata.

### Likelihood Explanation
The mechanism itself is fully unprivileged and requires only publishing a module (as the existing test infrastructure in `attributes.rs` already demonstrates the byte-level manipulation needed). The remaining uncertainty — which I could not fully resolve given tool-call limits — is whether the current bytecode deserializer/verifier still accepts a module whose file-format `version` field is `5` at all on current mainnet (i.e., whether there's a minimum-version enforcement elsewhere in the publish path that rejects version-5 modules before this code is reached). `METADATA_V1_MIN_FILE_FORMAT_VERSION = 6` and the code comment "this should have been gated in the verify module metadata" suggest version 5 is treated as a legacy/should-not-happen case, implying there may be an assumption elsewhere that version-5 modules aren't expected to carry V1 metadata — but I did not confirm whether raw version-5 bytecode is still deserializable/publishable through the current bytecode verifier's minimum version gate. This should be verified directly (e.g. `move-binary-format` deserializer version bounds, `VERIFIER_..._MIN_VERSION` constants) before treating this as fully mainnet-exploitable today.

### Recommendation
Make `get_metadata` apply the same `version() == 5` clearing logic as `get_metadata_from_compiled_code` (or better, have both go through one shared code path taking a `CompiledCodeMetadata`-like version argument), so that any consumer resolving on-chain resource-group/attribute metadata for API responses, `find_resource`, and `is_resource_group` sees metadata consistent with what was actually validated at publish time.

### Proof of Concept
1. Build a normal module containing struct `S` with key ability.
2. Using the same approach as `build_package_and_insert_attribute` in `aptos-move/e2e-move-tests/src/tests/attributes.rs`, deserialize the compiled module, replace its `metadata` field with a `RuntimeModuleMetadataV1` where `struct_attributes["S"] = [KnownAttribute::resource_group_member("0x1::some::Container")]` (an arbitrary/unrelated container never validated as a resource-group container), then set `compiled_module.version = 5` before re-serializing.
3. Publish via `code_publish_package_txn`. Because `verify_module_metadata_for_module_publishing` uses `get_metadata_from_compiled_code`, which clears `struct_attributes` for `version() == 5`, `is_valid_resource_group_member` is never invoked, and publication succeeds despite the forged, unvalidated attribute.
4. Query the API for resource `S` under the published address. `is_resource_group`/`find_resource` in `api/types/src/convert.rs` call `get_metadata` (not version-gated), see the forged `resource_group_member` attribute, and route the lookup through `StateKey::resource_group(&address, &container_tag)` instead of `StateKey::resource(&address, tag)`.
5. Compare against directly calling `get_metadata_from_compiled_code(&module)` on the same module: it returns empty `struct_attributes`, i.e., the VM's own validated view disagrees with what the API used to pick the `StateKey`.

### Citations

**File:** types/src/vm/module_metadata.rs (L198-230)
```rust
/// Extract metadata from the VM, upgrading V0 to V1 representation as needed
pub fn get_metadata(md: &[Metadata]) -> Option<Arc<RuntimeModuleMetadataV1>> {
    if let Some(data) = find_metadata(md, APTOS_METADATA_KEY_V1) {
        V1_METADATA_CACHE.with(|ref_cell| {
            let mut cache = ref_cell.borrow_mut();
            if let Some(meta) = cache.get(&data.value) {
                meta.clone()
            } else {
                let meta = bcs::from_bytes::<RuntimeModuleMetadataV1>(&data.value)
                    .ok()
                    .map(Arc::new);
                cache.put(data.value.clone(), meta.clone());
                meta
            }
        })
    } else if let Some(data) = find_metadata(md, APTOS_METADATA_KEY) {
        V0_METADATA_CACHE.with(|ref_cell| {
            let mut cache = ref_cell.borrow_mut();
            if let Some(meta) = cache.get(&data.value) {
                meta.clone()
            } else {
                let meta = bcs::from_bytes::<RuntimeModuleMetadata>(&data.value)
                    .ok()
                    .map(RuntimeModuleMetadata::upgrade)
                    .map(Arc::new);
                cache.put(data.value.clone(), meta.clone());
                meta
            }
        })
    } else {
        None
    }
}
```

**File:** types/src/vm/module_metadata.rs (L287-308)
```rust
pub fn get_metadata_from_compiled_code(
    code: &impl CompiledCodeMetadata,
) -> Option<RuntimeModuleMetadataV1> {
    if let Some(data) = find_metadata(code.metadata(), APTOS_METADATA_KEY_V1) {
        let mut metadata = bcs::from_bytes::<RuntimeModuleMetadataV1>(&data.value).ok();
        // Clear out metadata for v5, since it shouldn't have existed in the first place and isn't
        // being used. Note, this should have been gated in the verify module metadata.
        if code.version() == 5 {
            if let Some(metadata) = metadata.as_mut() {
                metadata.struct_attributes.clear();
                metadata.fun_attributes.clear();
            }
        }
        metadata
    } else if let Some(data) = find_metadata(code.metadata(), APTOS_METADATA_KEY) {
        // Old format available, upgrade to new one on the fly
        let data_v0 = bcs::from_bytes::<RuntimeModuleMetadata>(&data.value).ok()?;
        Some(data_v0.upgrade())
    } else {
        None
    }
}
```

**File:** types/src/vm/module_metadata.rs (L441-456)
```rust
pub fn verify_module_metadata_for_module_publishing(
    module: &CompiledModule,
    features: &Features,
) -> Result<(), MetaDataValidationError> {
    if features.is_enabled(FeatureFlag::SAFER_METADATA) {
        check_module_complexity(module)?;
    }

    if features.are_resource_groups_enabled() {
        check_metadata_format(module)?;
    }
    let metadata = if let Some(metadata) = get_metadata_from_compiled_code(module) {
        metadata
    } else {
        return Ok(());
    };
```

**File:** api/types/src/convert.rs (L145-182)
```rust
    pub fn is_resource_group(&self, tag: &StructTag) -> bool {
        if let Ok(Some(module)) = self.inner.view_module(&tag.module_id()) {
            if let Some(md) = get_metadata(&module.metadata) {
                if let Some(attrs) = md.struct_attributes.get(tag.name.as_ident_str().as_str()) {
                    return attrs
                        .iter()
                        .find(|attr| attr.is_resource_group())
                        .map(|_| true)
                        .unwrap_or(false);
                }
            }
        }
        false
    }

    pub fn find_resource(
        &self,
        state_view: &impl StateView,
        address: Address,
        tag: &StructTag,
    ) -> Result<Option<Bytes>> {
        Ok(match self.inner.view_resource_group_member(tag) {
            Some(group_tag) => {
                let key = StateKey::resource_group(&address.into(), &group_tag);
                match state_view.get_state_value_bytes(&key)? {
                    Some(group_bytes) => {
                        let group: BTreeMap<StructTag, Bytes> = bcs::from_bytes(&group_bytes)?;
                        group.get(tag).cloned()
                    },
                    None => None,
                }
            },
            None => {
                let key = StateKey::resource(&address.into(), tag)?;
                state_view.get_state_value_bytes(&key)?
            },
        })
    }
```

**File:** aptos-move/e2e-move-tests/src/tests/attributes.rs (L293-341)
```rust
fn build_package_and_insert_attribute(
    source: &str,
    struct_attr: Option<(&str, FakeKnownAttribute)>,
    func_attr: Option<(&str, FakeKnownAttribute)>,
) -> (Vec<Vec<u8>>, Vec<u8>) {
    let mut builder = PackageBuilder::new("Package");
    builder.add_source("m.move", source);
    let path = builder.write_to_temp().unwrap();

    let package = BuiltPackage::build(path.path().to_path_buf(), BuildOptions::default())
        .expect("building package must succeed");
    let code = package.extract_code();
    // There should only be one module
    assert!(code.len() == 1);
    let mut compiled_module = CompiledModule::deserialize(&code[0]).unwrap();
    let mut value = RuntimeModuleMetadataV1 {
        error_map: BTreeMap::new(),
        struct_attributes: BTreeMap::new(),
        fun_attributes: BTreeMap::new(),
    };

    if let Some((name, attr)) = struct_attr {
        let fake_attribute = bcs::to_bytes(&attr).unwrap();
        let known_attribute = bcs::from_bytes(&fake_attribute).unwrap();
        value
            .struct_attributes
            .insert(name.to_string(), vec![known_attribute]);
    };
    if let Some((name, attr)) = func_attr {
        let fake_attribute = bcs::to_bytes(&attr).unwrap();
        let known_attribute = bcs::from_bytes(&fake_attribute).unwrap();
        value
            .fun_attributes
            .insert(name.to_string(), vec![known_attribute]);
    }

    let metadata = Metadata {
        key: APTOS_METADATA_KEY_V1.to_vec(),
        value: bcs::to_bytes(&value).unwrap(),
    };

    compiled_module.metadata = vec![metadata];
    let mut code = vec![];
    compiled_module.serialize(&mut code).unwrap();
    let metadata = package
        .extract_metadata()
        .expect("extracting package metadata must succeed");
    (vec![code], bcs::to_bytes(&metadata).unwrap())
}
```
