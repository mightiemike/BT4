## Finding

### Title
Unbounded recursion in compiled-class-hash bytecode segment hashing enables stack-overflow DoS from a single Declare transaction - ([File: crates/starknet_api/src/contract_class/compiled_class_hash.rs])

### Summary
`bytecode_hash_node` in `crates/starknet_api/src/contract_class/compiled_class_hash.rs` recursively walks the CASM `bytecode_segment_lengths` tree (`NestedIntList`) with no depth limit. [1](#0-0)  This tree structure is derived by the Sierra→CASM compiler from the *attacker-supplied* Sierra program submitted in a `Declare` transaction, so its nesting depth is effectively controlled by whoever declares the class. Unlike the CPU/memory limits enforced on the actual compilation step, the subsequent hash computation runs unconstrained in the calling (non-sandboxed) process.

### Finding Description
`SierraCompiler::compile` first invokes the Sierra→Casm compiler binary in a resource-limited subprocess (`ResourceLimits::new` bounding CPU time and memory), then, back in the parent process, calls `executable_class.hash(&HashVersion::V2)` directly — with no resource limits at all: [2](#0-1) 

That `hash()` call eventually reaches `bytecode_hash`, which recurses into `bytecode_hash_node` once per node of the compiled class's `bytecode_segment_lengths` structure: [3](#0-2)  Each non-leaf node triggers a further recursive call per child with no maximum-depth check, and the function itself is generic (used for both Poseidon/V1 and Blake2/V2 hashing, and for both class declaration and any later re-computation of the class hash).

Only the total bytecode *size* is bounded (`max_contract_bytecode_size` = 81920 in the gateway's static config, and `DEFAULT_MAX_BYTECODE_SIZE`/`max_bytecode_size` enforced by the compiler binary): [4](#0-3)  nothing bounds how deeply the segment tree describing that bytecode may be nested. A crafted Sierra program (e.g., many nested/chained small functions or branches, each compiling to its own bytecode segment) can be compiled by the (legitimate) sierra compiler into a CASM class whose `bytecode_segment_lengths` is a deeply/linearly nested `NestedIntList::Node(...)` chain up to a size on the order of the bytecode-size limit. Recursing through such a structure in `bytecode_hash_node` (which is not tail-call-optimized and allocates a `Vec` via `collect_vec()` at every level) will exhaust the thread's stack.

Because the hash step happens outside the subprocess sandbox that protects the compiler proper, an overflow here crashes the calling process itself (the `SierraCompiler`/class-manager component, or any other component/binary that computes compiled-class hashes, e.g. re-execution, migration checks in the bouncer, or the analogous Cairo implementation used by the Starknet OS at `apollo_starknet_os_program/.../blake_compiled_class_hash.cairo`/`poseidon_compiled_class_hash.cairo`).

### Impact Explanation
A crash of the class-compilation/class-manager service (or any sequencer component that must (re)compute a declared class's compiled-class hash to validate a `Declare` transaction or verify state) constitutes a network-availability failure: the affected node can no longer process new `Declare` transactions (and potentially any transaction touching the malicious class, since class hash re-verification/migration logic also calls this hashing path), directly matching "a network unable to confirm new transactions" from the acceptance criteria. This is reachable by any single unprivileged contract declarer with no special privileges — exactly analogous to the reachability characteristics of CVE-2019-2693 (crash/hang triggerable by a low-privileged, network-reachable actor).

### Likelihood Explanation
Likelihood is high for any implementation exposing this hashing path without depth limiting: an attacker only needs to submit one well-formed but adversarially structured Sierra contract via a normal `Declare` transaction (paying ordinary declare fees) that compiles into a CASM class with a highly nested `bytecode_segment_lengths` tree. No special protocol knowledge beyond crafting deeply-nested Cairo/Sierra function/branch structure is required, and the existing size-based limits (`max_contract_bytecode_size`, `max_bytecode_size`) do nothing to bound the tree depth.

### Recommendation
- Bound the recursion depth (or convert `bytecode_hash_node` to an explicit iterative/stack-based traversal with a bounded work-stack) in `crates/starknet_api/src/contract_class/compiled_class_hash.rs`.
- Reject classes at declaration/compile time whose `bytecode_segment_lengths` nesting exceeds a configured maximum depth, independent of total bytecode size.
- Ensure the hash-computation step also runs under the same resource-limited/sandboxed execution as the Sierra→Casm compilation step in `apollo_compile_to_casm`/`apollo_compile_to_native`, so that a crash there does not take down the parent service.
- Apply the equivalent fix to the Cairo implementation of the bytecode segment hashing used by the Starknet OS (`blake_compiled_class_hash.cairo` / `poseidon_compiled_class_hash.cairo`) to avoid divergence/hang during OS re-execution.

### Proof of Concept
1. Author a Sierra program (or use existing tooling) that compiles into CASM with a long, linear chain of nested function/branch segments, e.g. `Node([Node([Node([...Leaf(1)...])])])` nested tens of thousands of levels deep, while staying within `max_contract_bytecode_size` (81920) and `max_bytecode_size` limits.
2. Submit this class via a normal `Declare` transaction (`RpcTransaction::Declare`) through the gateway.
3. When `SierraCompiler::compile` calls `executable_class.hash(&HashVersion::V2)` outside the sandboxed compiler subprocess [5](#0-4) , the recursive call chain into `bytecode_hash_node` overflows the thread stack, crashing the class-manager/compiler process and halting processing of further declare (and dependent) transactions on that node.

### Citations

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L96-132)
```rust
fn bytecode_hash<H, NL>(bytecode: &[Felt], bytecode_segment_lengths: &NL) -> Felt
where
    H: StarkHash,
    NL: HashableNestedIntList,
{
    let mut bytecode_iter = bytecode.iter().copied();
    let (len, bytecode_hash) =
        bytecode_hash_node::<H, NL>(&mut bytecode_iter, bytecode_segment_lengths);
    assert_eq!(len, bytecode.len());
    bytecode_hash
}

/// Computes the hash of a bytecode segment. See the documentation of `bytecode_hash_node` in
/// the Starknet OS.
/// Returns the length of the processed segment and its hash.
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
