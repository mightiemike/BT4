## Analog Found: Unbounded Recursion in Compiled-Class-Hash Bytecode Segment Hashing

### Title
Unbounded recursion in `bytecode_hash_node` over attacker-influenced CASM segment tree can crash the sequencer node - (File: `crates/starknet_api/src/contract_class/compiled_class_hash.rs`)

### Summary
The Starknet compiled-class-hash algorithm hashes a contract's CASM bytecode by recursing over a `NestedIntList` "segment tree" (`bytecode_segment_lengths`) that is produced by the Sierra→CASM compiler and shipped inside `CasmContractClass`. The recursive helper `bytecode_hash_node` in `crates/starknet_api/src/contract_class/compiled_class_hash.rs` has no depth bound, mirroring the unbounded-recursion pattern in CVE-2017-5839 (`gst_riff_create_audio_caps` recursing on attacker-controlled nested `WAVEFORMATEX`). Because the nesting depth of this segment tree is derived from the structure of an attacker-submitted (declared) Sierra program, a crafted contract can force very deep recursion when its compiled-class hash is computed — and that hash computation runs unsandboxed, directly inside the sequencer/gateway process (unlike the Sierra→CASM compilation step itself, which is explicitly resource-limited in a subprocess).

### Finding Description
`bytecode_hash_node` recurses once per tree level of `bytecode_segment_lengths: NestedIntList`: [1](#0-0) 

`NestedIntList` is populated by `CasmContractClass::bytecode_segment_lengths`, which the Sierra→CASM compiler (`cairo-lang-starknet-classes`) generates to reflect the nested control-flow/segmentation structure of the compiled program. This is fetched via `HashableCompiledClass::get_bytecode_segment_lengths` for `CasmContractClass`: [2](#0-1) 

Note that a *different* recursive builder in the blockifier explicitly caps nesting to depth 1 (`assert!(segmentation_depth <= 1, ...)`) for its own `NestedFeltCounts` structure used in gas-estimation: [3](#0-2) 

but the `starknet_api::contract_class::compiled_class_hash::bytecode_hash_node` function used for the canonical **compiled class hash** has no equivalent depth cap.

Critically, unlike the Sierra→CASM compilation itself — which runs in a separate, resource-limited subprocess (`ResourceLimits::new(max_cpu_time, _, max_memory_usage)`): [4](#0-3) 

the hash computation over the resulting `CasmContractClass` runs immediately afterward, in-process, with no isolation or recursion guard, as part of `SierraCompiler::compile`: [5](#0-4) 

This same unbounded `hash()` call is also reachable from ordinary state/execution paths (compiled-class-hash lookups) and from `ContractClass::compiled_class_hash`, meaning any sequencer component that re-derives a class hash for a declared class touches this recursive routine: [6](#0-5) [7](#0-6) 

### Impact Explanation
A single declared Sierra class, submitted by an unprivileged declarer, can pass the sandboxed compile step (bytecode ≤ `max_contract_bytecode_size` = 81920 felts, per `crates/apollo_node/resources/config_schema.json`) yet still produce a CASM whose segment tree is deeply nested (segmentation follows function/branch structure, not raw byte count, so many thousands of shallow nested branches can fit well within the byte budget). When the gateway then calls `executable_class.hash(&HashVersion::V2)` in its own process to compute the class hash, the unbounded native-stack recursion in `bytecode_hash_node` can exhaust the thread stack and crash the sequencer/gateway process. Because this same hash routine is invoked again later during stateful re-validation, execution, and Starknet OS re-execution (whenever a compiled-class hash needs to be re-derived), a single malicious declare transaction can repeatedly crash sequencer processes across the network, preventing confirmation of new transactions — a network-availability impact analogous to the GStreamer stack-overflow DoS.

### Likelihood Explanation
Likelihood is only moderate-to-uncertain: the attacker needs the external Sierra→CASM compiler to actually emit a segment tree with sufficient nesting depth from a Sierra program that still fits the enforced size limits, and native stack overflows require enough recursive frames given each frame allocates a `Vec` via `collect_vec()`. This depends on the specific segmentation behavior of the `cairo-lang-starknet-classes` compiler version in use, which is outside this repository, so the achievable depth cannot be fully confirmed from the sequencer code alone — but the recursive code path itself is unmistakably missing any depth guard, in contrast to a sibling code path in the same codebase that explicitly asserts depth ≤ 1.

### Recommendation
Convert `bytecode_hash_node` to an explicit iterative/stack-based traversal (or add an enforced maximum segmentation depth, mirroring the `assert!(segmentation_depth <= 1, ...)` guard already used in `blockifier/src/execution/contract_class.rs`), and reject `CasmContractClass` instances whose `bytecode_segment_lengths` exceed that bound before hashing. Consider validating/bounding this at class-manager ingestion time as well, so no downstream re-execution or OS component can be crashed by a previously-accepted class.

### Proof of Concept
1. Craft a Sierra program (via a Cairo1 contract) that compiles to a CASM whose control flow produces deep nested segmentation while remaining under `max_contract_bytecode_size`/`max_contract_class_object_size` (e.g., deeply nested `if`/`match` chains with minimal per-branch bytecode, replicated thousands of times) so `bytecode_segment_lengths` becomes a `NestedIntList::Node` chain thousands of levels deep.
2. Submit it as a V3 declare transaction to the gateway.
3. Observe `SierraCompiler::compile` (`crates/apollo_compile_to_casm/src/lib.rs:67-69`) call `executable_class.hash(&HashVersion::V2)`, which invokes `bytecode_hash_node` recursively with no bound, causing native stack exhaustion and process termination in the gateway/sequencer process.

### Citations

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L111-132)
```rust
fn bytecode_hash_node<H, NL>(iter: &mut impl Iterator<Item = Felt>, node: &NL) -> (usize, Felt)
where
    H: StarkHash,
    NL: HashableNestedIntList,
{
    if node.is_leaf() {
        let len = node.get_segment_length();
        let data = iter.take(len).collect_vec();
        assert_eq!(data.len(), len);
        (len, H::hash_array(&data))
    } else {
        // Compute `1 + poseidon(len0, hash0, len1, hash1, ...)`.
        let inner_nodes = node
            .iter_children()
            .map(|child| bytecode_hash_node::<H, NL>(iter, child))
            .collect_vec();
        let hash = H::hash_array(
            &inner_nodes.iter().flat_map(|(len, hash)| [Felt::from(*len), *hash]).collect_vec(),
        ) + Felt::ONE;
        (inner_nodes.iter().map(|(len, _)| len).sum(), hash)
    }
}
```

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L180-205)
```rust
impl HashableCompiledClass<CasmContractEntryPoint, NestedIntList> for CasmContractClass {
    fn get_hashable_l1_entry_points(&self) -> &[CasmContractEntryPoint] {
        &self.entry_points_by_type.l1_handler
    }

    fn get_hashable_external_entry_points(&self) -> &[CasmContractEntryPoint] {
        &self.entry_points_by_type.external
    }

    fn get_hashable_constructor_entry_points(&self) -> &[CasmContractEntryPoint] {
        &self.entry_points_by_type.constructor
    }

    fn get_bytecode(&self) -> Vec<Felt> {
        self.bytecode.iter().map(|big_uint| Felt::from(&big_uint.value)).collect()
    }

    /// Returns the lengths of the bytecode segments.
    /// If the length field is missing, the entire bytecode is considered a single segment.
    fn get_bytecode_segment_lengths(&self) -> Cow<'_, NestedIntList> {
        match &self.bytecode_segment_lengths {
            Some(bytecode_segment_lengths) => Cow::Borrowed(bytecode_segment_lengths),
            None => Cow::Owned(NestedIntList::Leaf(self.bytecode.len())),
        }
    }
}
```

**File:** crates/blockifier/src/execution/contract_class.rs (L163-194)
```rust
    fn new_inner(
        bytecode_segment_lengths: &NestedIntList,
        bytecode: &[BigUintAsHex],
        segmentation_depth: usize,
    ) -> (Self, usize) {
        assert!(segmentation_depth <= 1, "Only supported for segmentation depth at most 1.");

        match bytecode_segment_lengths {
            NestedIntList::Leaf(len) => {
                let felt_size_groups = FeltSizeCount::from(&bytecode[..*len]);
                (NestedFeltCounts::Leaf(*len, felt_size_groups), *len)
            }
            NestedIntList::Node(segments_vec) => {
                let mut total_felt_count = 0;
                let mut segments = Vec::with_capacity(segments_vec.len());

                for segment in segments_vec {
                    // Recurse into the segment layout.
                    let (segment, felt_count) = Self::new_inner(
                        segment,
                        &bytecode[total_felt_count..],
                        segmentation_depth + 1,
                    );
                    // Accumulate the count from the segment`s subtree.
                    total_felt_count += felt_count;
                    segments.push(segment);
                }

                (NestedFeltCounts::Node(segments), total_felt_count)
            }
        }
    }
```

**File:** crates/apollo_compile_to_casm/src/compiler.rs (L30-55)
```rust
    pub fn compile(
        &self,
        contract_class: ContractClass,
    ) -> Result<CasmContractClass, CompilationUtilError> {
        let compiler_binary_path = &self.path_to_binary;
        let additional_args = &[
            "--add-pythonic-hints",
            "--max-bytecode-size",
            &self.config.max_bytecode_size.to_string(),
            "--allowed-libfuncs-list-name",
            if self.config.audited_libfuncs_only { "audited" } else { "all" },
        ];
        let resource_limits = ResourceLimits::new(
            Some(self.config.max_cpu_time),
            None,
            Some(self.config.max_memory_usage),
        );

        let stdout = compile_with_args(
            compiler_binary_path,
            contract_class,
            additional_args,
            resource_limits,
        )?;
        Ok(serde_json::from_slice::<CasmContractClass>(&stdout)?)
    }
```

**File:** crates/apollo_compile_to_casm/src/lib.rs (L60-74)
```rust
    pub fn compile(&self, class: RawClass) -> SierraCompilerResult<RawExecutableHashedClass> {
        let class = SierraContractClass::try_from(class)?;
        let sierra_version =
            class.get_sierra_version().map_err(SierraCompilerError::SierraVersionFormat)?;
        let class = into_contract_class_for_compilation(&class);

        // TODO(Elin): handle resources (whether here or an infra. layer load-balancing).
        let executable_class = self.compiler.compile(class)?;
        // TODO(Elin): consider spawning a worker for hash calculation.
        let executable_class_hash = executable_class.hash(&HashVersion::V2);
        let executable_class = ContractClass::V1((executable_class, sierra_version));
        let executable_class = RawExecutableClass::try_from(executable_class)?;

        Ok((executable_class, executable_class_hash))
    }
```

**File:** crates/starknet_api/src/contract_class/structs.rs (L53-61)
```rust
impl ContractClass {
    pub fn compiled_class_hash(&self) -> CompiledClassHash {
        match self {
            ContractClass::V0(_) => panic!("Cairo 0 doesn't have compiled class hash."),
            ContractClass::V1((casm_contract_class, _sierra_version)) => {
                casm_contract_class.hash(&HashVersion::V2)
            }
        }
    }
```

**File:** crates/blockifier/src/state/utils.rs (L12-27)
```rust
#[allow(dead_code)]
pub fn get_compiled_class_hash_v2(
    state_reader: &impl StateReader,
    class_hash: ClassHash,
    _compiled_class: &RunnableCompiledClass,
) -> StateResult<CompiledClassHash> {
    match state_reader.get_compiled_class(class_hash) {
        Ok(RunnableCompiledClass::V0(_)) | Err(StateError::UndeclaredClassHash(_)) => {
            Err(StateError::MissingCompiledClassHashV2(class_hash))
        }
        Ok(RunnableCompiledClass::V1(class)) => Ok(class.hash(&HashVersion::V2)),
        #[cfg(feature = "cairo_native")]
        Ok(RunnableCompiledClass::V1Native(class)) => Ok(class.hash(&HashVersion::V2)),
        Err(e) => Err(e),
    }
}
```
