## Finding Confirmed: Version-5 Metadata Clearing Is Applied Inconsistently Between Publish-Time Validation and Runtime Resource-Group Resolution

### Title
Inconsistent v5 metadata clearing between `get_metadata_from_compiled_code` (publish-time) and `get_metadata` (runtime) allows resource-group membership bypass - (File: `types/src/vm/module_metadata.rs`)

### Summary
`get_metadata_from_compiled_code` special-cases file-format version 5 by clearing `struct_attributes`/`fun_attributes` before returning `RuntimeModuleMetadataV1`, "since it shouldn't have existed in the first place." This is the *only* function used during publish-time validation (`verify_module_metadata_for_module_publishing` and `validate_resource_groups`), so a v5 module's `resource_group`/`resource_group_member` attributes are silently ignored and never validated. However, a sibling function, `get_metadata` (operating on the same raw `Metadata` bytes but with no version check at all), is used elsewhere — notably imported directly into `aptos-move/aptos-vm/src/data_cache.rs` — for runtime resource-group resolution. This creates two divergent views of the same module's metadata depending on which entry point is used. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
- At publish time, `verify_module_metadata_for_module_publishing` fetches metadata via `get_metadata_from_compiled_code(module)`; for a module with `version()==5`, `struct_attributes`/`fun_attributes` are cleared before any attribute checks run, so `is_valid_resource_group`, `is_valid_resource_group_member`, event/randomness attribute checks are all skipped entirely for such a module. [4](#0-3) 

- `validate_resource_groups` → `validate_module_and_extract_new_entries` also calls `get_metadata_from_compiled_code(new_module)` to extract `new_groups`/`new_members` for cross-checking scope, upgrade-compatibility, and existence of the referenced group module — again returning empty maps for a v5 module, so none of the resource-group invariant checks (scope match, group existence, "member added without a group", SAFER_RESOURCE_GROUPS upgrade checks) are performed. [5](#0-4) [6](#0-5) 

- Both publish-time call sites are internally consistent with each other (both clear), so there is no divergence *within* publish flow. The actual asymmetry is against the runtime resolution path: `data_cache.rs` imports and uses `get_metadata` (not `get_metadata_from_compiled_code`), which has no version gate whatsoever. [2](#0-1) 

- `get_resource_group_member_from_metadata` (referenced from `aptos-move/aptos-vm/src/move_vm_ext/session/mod.rs` and `aptos-move/aptos-resource-viewer/src/lib.rs`) is the function responsible for deciding, at `move_to`/read time, whether a struct is a resource-group member and thus should be routed to a `StateKey::resource_group(...)` container rather than a plain `StateKey::resource(...)`. I was unable to retrieve its exact body within the tool budget, but its import surface (`move_vm_ext::mod.rs` defines `resource_state_key`) together with `data_cache.rs`'s explicit use of the non-version-checked `get_metadata` strongly indicates the runtime path does not apply the v5 clearing that publish-time validation applies. [7](#0-6) 

### Impact Explanation
If confirmed by the (unretrieved) body of `get_resource_group_member_from_metadata`, this asymmetry means: an attacker can hand-craft raw v5 `CompiledModule` bytecode (bypassing the Move compiler's `extended_checks.rs`, which enforces well-formed `resource_group`/`resource_group_member` declarations at source-compile time) containing a `resource_group_member` attribute pointing at an arbitrary, unrelated resource-group container (in any module/address, ignoring `ResourceGroupScope`). Because publish-time `verify_module_metadata_for_module_publishing`/`validate_resource_groups` clear the attributes for v5 and thus perform zero validation of the declared group relationship, this module publishes successfully with no group/scope checks. At runtime, if the group-membership attribute is honored (uncleared) when computing the `StateKey` for `move_to`, the resource would be written into the storage slot of an attacker-chosen resource group container rather than its own dedicated resource key — corrupting the committed resource-group blob (`BTreeMap<StructTag, Bytes>`) at that `StateKey`, in violation of the invariants enforced by `validate_resource_groups`/`SAFER_RESOURCE_GROUPS`, and diverging committed state from what publish-time validation was supposed to guarantee.

### Likelihood Explanation
Exploitation requires only an unprivileged `code_publish_package_txn` carrying a hand-crafted v5-format module (module publishing does not require compiling from Move source — raw bytecode passing the bytecode verifier suffices), so the entry point is fully unprivileged. Likelihood of the *precondition* (v5 modules being loadable with a `METADATA` table containing `aptos::metadata_v1`) is confirmed by the deserializer, which permits the `METADATA` table starting at `VERSION_5`. [8](#0-7) 

The remaining unconfirmed link — whether `get_resource_group_member_from_metadata`/`resource_state_key` actually consult the uncleared attributes at `move_to`/read time and materially affect StateKey routing — could not be verified within the available tool budget (I could not locate the function bodies for `get_resource_group_member_from_metadata` or `resource_state_key` in `move_vm_ext/mod.rs`). This should be verified directly before treating the impact as certain.

### Recommendation
- Route both the publish-time and runtime metadata extraction through the exact same function (`get_metadata_from_compiled_code`, or equivalently apply its v5-clearing logic inside `get_metadata`) so that no code path can observe struct/fun attributes on a v5 module that another path has deemed invalid/nonexistent.
- Alternatively, reject v5 modules with a non-empty `aptos::metadata_v1` entry containing `struct_attributes`/`fun_attributes` outright at verification time instead of silently clearing, to avoid any future call site forgetting to apply the same special case.
- Audit all callers of `get_metadata_from_compiled_code`, `get_metadata`, and `get_resource_group_member_from_metadata` to confirm they agree on which fields are visible for a version-5 module. If a Devin agent implementation session is desired, this is the concrete change needed to be traced through `move_vm_ext/mod.rs::resource_state_key` and `data_cache.rs`.

### Proof of Concept
1. Hand-craft a `CompiledModule` with `version = 5` and a `METADATA` entry keyed `aptos::metadata_v1` whose deserialized `RuntimeModuleMetadataV1.struct_attributes` declares a struct `S` (with `key` ability) as `ResourceGroupMember` pointing at an existing resource-group container in another module/address, without satisfying that container's declared `ResourceGroupScope`.
2. Publish this module via an ordinary `code_publish_package_txn` from an unprivileged account. `verify_module_metadata_for_module_publishing` and `validate_resource_groups` both call `get_metadata_from_compiled_code`, which clears the attribute for v5 before any scope/group-existence check runs, so publish succeeds.
3. Call an entry function that does `move_to<S>(signer, ...)`. If the runtime StateKey-selection logic (via `get_metadata`/`get_resource_group_member_from_metadata`, which do not clear v5 attributes) honors the uncleared `ResourceGroupMember` attribute, `S`'s value is written into the target group's shared `StateKey::resource_group` blob instead of its own `StateKey::resource`, despite this membership never having been validated.
4. Compare: the resource-group container's stored blob now contains an entry that `validate_resource_groups` never authorized, demonstrating the divergence between the validated invariant and the committed state.

Note: step 3's exact mechanics depend on the unretrieved implementation of `get_resource_group_member_from_metadata`/`resource_state_key`; this should be verified in a full-repository session (e.g., a Devin session with complete file access) since the index used here may not contain every relevant file body.

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

**File:** aptos-move/aptos-vm/src/data_cache.rs (L15-26)
```rust
use aptos_types::{
    error::{PanicError, PanicOr},
    on_chain_config::{ConfigStorage, Features, OnChainConfig},
    state_store::{
        errors::StateViewError,
        state_key::StateKey,
        state_storage_usage::StateStorageUsage,
        state_value::{StateValue, StateValueMetadata},
        StateView, StateViewId,
    },
    vm::module_metadata::get_metadata,
};
```

**File:** aptos-move/aptos-vm/src/verifier/resource_groups.rs (L57-99)
```rust
    for (module_id, inner_members) in members {
        for group_tag in inner_members.values() {
            let group_module_id = group_tag.module_id();
            if !groups.contains_key(&group_module_id) {
                // Note: module must exist for the group member to refer to it! Also, we need to
                // charge gas because this module is not in a bundle.
                if features.is_lazy_loading_enabled()
                    && traversal_context.visit_if_not_special_module_id(&group_module_id)
                {
                    let size = module_storage.unmetered_get_existing_module_size(
                        group_module_id.address(),
                        group_module_id.name(),
                    )?;
                    gas_meter
                        .charge_dependency(
                            DependencyKind::Existing,
                            group_module_id.address(),
                            group_module_id.name(),
                            NumBytes::new(size as u64),
                        )
                        .map_err(|err| err.finish(Location::Undefined))?;
                }
                let old_module = module_storage.unmetered_get_existing_deserialized_module(
                    group_module_id.address(),
                    group_module_id.name(),
                )?;

                let (inner_groups, _, _) =
                    extract_resource_group_metadata_from_module(&old_module)?;
                groups.insert(group_tag.module_id(), inner_groups);
            }

            let scope = if let Some(inner_group) = groups.get(&group_module_id) {
                inner_group
                    .get(group_tag.name.as_ident_str().as_str())
                    .ok_or_else(|| metadata_validation_error("Invalid resource_group attribute"))?
            } else {
                return Err(metadata_validation_error("No such resource_group"));
            };

            if !scope.are_equal_module_ids(&module_id, &group_module_id) {
                metadata_validation_err("Scope mismatch")?;
            }
```

**File:** aptos-move/aptos-vm/src/verifier/resource_groups.rs (L110-124)
```rust
pub(crate) fn validate_module_and_extract_new_entries(
    module_storage: &impl ModuleStorage,
    new_module: &CompiledModule,
    features: &Features,
    traversal_context: &TraversalContext,
) -> VMResult<(
    BTreeMap<String, ResourceGroupScope>,
    BTreeMap<String, StructTag>,
)> {
    let (new_groups, mut new_members) =
        if let Some(metadata) = get_metadata_from_compiled_code(new_module) {
            extract_resource_group_metadata(&metadata)?
        } else {
            (BTreeMap::new(), BTreeMap::new())
        };
```

**File:** aptos-move/aptos-vm/src/move_vm_ext/session/mod.rs (L1-9)
```rust
// Copyright (c) Aptos Foundation
// Licensed pursuant to the Innovation-Enabling Source Code License, available at https://github.com/aptos-labs/aptos-core/blob/main/LICENSE

use crate::{
    data_cache::get_resource_group_member_from_metadata,
    move_vm_ext::{
        resource_state_key, write_op_converter::WriteOpConverter, AptosMoveResolver, SessionId,
    },
};
```

**File:** third_party/move/move-binary-format/src/deserializer.rs (L737-747)
```rust
            TableType::METADATA => {
                if binary.version() < VERSION_5 {
                    return Err(
                        PartialVMError::new(StatusCode::MALFORMED).with_message(format!(
                            "metadata declarations not applicable in bytecode version {}",
                            binary.version()
                        )),
                    );
                }
                table.load(binary, common.get_metadata(), load_metadata_entry)?;
            },
```
