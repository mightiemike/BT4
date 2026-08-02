## Title
Version-5 metadata clearing is applied inconsistently between `get_metadata` and `get_metadata_from_compiled_code`, allowing randomness/view attribute validation to be bypassed for `file_format` version-5 modules - (File: `types/src/vm/module_metadata.rs`)

### Summary
`get_metadata_from_compiled_code` explicitly strips `fun_attributes`/`struct_attributes` when `code.version() == 5` (with a comment stating this metadata "shouldn't have existed in the first place and isn't being used"), but `get_metadata` — which operates directly on a raw `&[Metadata]` slice and has no notion of `file_format` version at all — never performs this clearing. Both functions read the exact same underlying `APTOS_METADATA_KEY_V1` bytes from the module, so an attacker-crafted v5 module can carry a `Randomness`/`ViewFunction` attribute that is invisible to the publish-time validator but visible to any code path that calls `get_metadata` instead.

### Finding Description [1](#0-0) 
`get_metadata` takes a bare `&[Metadata]`, decodes `APTOS_METADATA_KEY_V1` bytes, and caches the result keyed only by the raw bytes — there is no `version` field or check anywhere in this function. [2](#0-1) 
`get_metadata_from_compiled_code` decodes the identical bytes but additionally checks `code.version() == 5` and, if true, clears `struct_attributes` and `fun_attributes` on the *returned, decoded* object before handing it back. Critically, this clearing only mutates the in-memory `RuntimeModuleMetadataV1` struct that is returned — it does **not** rewrite or invalidate the underlying metadata bytes stored in the module (`code.metadata()`), and it does not gate `get_metadata` in any way. [3](#0-2) 
`verify_module_metadata_for_module_publishing` (the publish-time gate) calls `get_metadata_from_compiled_code`, so for a v5 module it receives an *already-cleared* metadata object. The subsequent loop that validates each `fun_attributes` entry via `is_valid_unbiasable_function`/`is_valid_view_function` therefore iterates over an empty map and validates nothing — the module publishes successfully regardless of whether the annotated function is actually a private, non-public entry function (the safety property `is_valid_unbiasable_function` is designed to enforce). [4](#0-3) 
Meanwhile, `get_randomness_annotation_for_entry_function` — used to decide whether an entry function is allowed to consume the framework's unbiasable randomness API — calls `get_metadata`, not `get_metadata_from_compiled_code`. Because `get_metadata` has no version awareness, it will return the original, un-cleared `fun_attributes` (including the `Randomness` attribute) for the very same v5 module whose attributes were stripped at publish time.

Net effect: a v5 `CompiledModule` can be constructed with `APTOS_METADATA_KEY_V1` metadata marking a **public** entry function as `#[randomness]`. Because `check_metadata_format` (called from the same publishing path) only validates that the bytes deserialize and the key is known — it does not check `code.version()` against `METADATA_V1_MIN_FILE_FORMAT_VERSION` (6) — and because the clearing in `get_metadata_from_compiled_code` neuters the subsequent attribute-validation loop for v5, the publish-time check `is_valid_unbiasable_function` (which requires the function be a private, non-public entry function to prevent test-and-abort bias attacks) is never invoked for this function. At execution time, `get_randomness_annotation_for_entry_function` (via `get_metadata`) still honors the `Randomness` annotation because it reads the raw, un-cleared bytes.

### Impact Explanation
This breaks the intended invariant that only functions verified to be non-biasable (private entry functions) can be treated as randomness-consuming. A public function that never underwent that check could be granted access to the on-chain unbiasable-randomness API/semantics, enabling test-and-abort style bias attacks against protocol randomness — a genuine state-integrity/fairness concern for any logic (e.g., lotteries, NFT rarity, validator selection) that trusts the randomness-safety guarantee.

However, I want to flag an important caveat on the "cross-node divergence" framing in the question: `get_metadata` and `get_metadata_from_compiled_code` are both deterministic pure functions over module bytes that run identically on every validator/full node. This inconsistency is a **within-VM-logic bug** (one call site validates against stale/cleared data, another call site trusts un-cleared data), not a source of divergence *between* nodes — every node reaches the same (wrong) outcome. It does not, by itself, produce differing committed state across replicas or a hard fork; it is a deterministic bypass of an intended safety check that all nodes apply identically.

### Likelihood Explanation
I was unable to fully verify, within the available search scope, whether `get_randomness_annotation_for_entry_function` (or another consumer of `get_metadata`) is actually reachable from the transaction-execution path for entry functions in a way that gates real randomness API access at runtime (my searches for its call sites and for `CompiledCodeMetadata`'s `version()` implementation in `aptos-vm/src/aptos_vm.rs` did not return results, likely due to index/search limitations). This gap means I cannot confirm the full end-to-end runtime reachability of the bypass, only the asymmetry within `module_metadata.rs` itself.

### Recommendation
- Make `get_metadata` version-aware (thread the `file_format` version through, or key the cache/clearing logic identically to `get_metadata_from_compiled_code`), so both functions agree on whether v5 `fun_attributes`/`struct_attributes` are honored.
- Alternatively, reject publishing of any module with `file_format` version `< METADATA_V1_MIN_FILE_FORMAT_VERSION` (6) that carries `APTOS_METADATA_KEY_V1` metadata outright in `check_metadata_format`, rather than silently clearing the decoded struct only at one call site.
- Audit all other callers of `get_metadata` to confirm whether any of them (e.g., randomness annotation lookup, view-function dispatch, resource-group resolution) are reachable for a v5 module in production and would be affected by this asymmetry.

### Proof of Concept
1. Hand-craft (or use a modified compiler backend) to emit a `CompiledModule` with `module.version = 5` whose `metadata` section contains an `APTOS_METADATA_KEY_V1` entry serializing a `RuntimeModuleMetadataV1` with `fun_attributes` mapping a **public** entry function to `KnownAttribute::randomness(None)`.
2. Publish it: `verify_module_metadata_for_module_publishing` calls `get_metadata_from_compiled_code(module)`, which detects `version() == 5` and clears `fun_attributes` before the validation loop runs, so `is_valid_unbiasable_function` is never invoked and publishing succeeds despite the function being public.
3. At execution/dispatch time, call `get_randomness_annotation_for_entry_function(&entry_func, module.metadata())`, which internally calls `get_metadata(md)` — this has no version check, decodes the same raw bytes, and returns `Some(RandomnessAnnotation)` for the public function, showing the two code paths disagree on whether this function's `Randomness` attribute is valid/present.

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

**File:** types/src/vm/module_metadata.rs (L232-250)
```rust
/// For the specified entry function, tries to find randomness attribute in its metadata. If it
/// does not exist, [None] is returned.
pub fn get_randomness_annotation_for_entry_function(
    entry_func: &EntryFunction,
    metadata: &[Metadata],
) -> Option<RandomnessAnnotation> {
    get_metadata(metadata).and_then(|metadata| {
        metadata
            .fun_attributes
            .get(entry_func.function().as_str())
            .map(|attrs| {
                attrs
                    .iter()
                    .filter_map(KnownAttribute::try_as_randomness_annotation)
                    .next()
            })
            .unwrap_or(None)
    })
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

**File:** types/src/vm/module_metadata.rs (L441-482)
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

    let functions = module
        .function_defs
        .iter()
        .map(|func_def| {
            let func_handle = module.function_handle_at(func_def.function);
            let name = module.identifier_at(func_handle.name);
            (name, (func_handle, func_def))
        })
        .collect::<BTreeMap<_, _>>();

    for (fun, attrs) in &metadata.fun_attributes {
        for attr in attrs {
            if attr.is_view_function() {
                is_valid_view_function(module, &functions, fun)?;
            } else if attr.is_randomness() {
                is_valid_unbiasable_function(&functions, fun)?;
            } else {
                return Err(AttributeValidationError {
                    key: fun.clone(),
                    attribute: attr.kind,
                }
                .into());
            }
        }
    }
```
