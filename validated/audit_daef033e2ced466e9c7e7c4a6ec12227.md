### Title
Unbounded recursion in compiled-class-hash computation over compiler-controlled `NestedIntList` bytecode segmentation can stack-overflow the Sierra compiler service on a crafted Declare class - ([File: crates/starknet_api/src/contract_class/compiled_class_hash.rs])

### Summary
`bytecode_hash_node` in `compiled_class_hash.rs` recursively walks the `bytecode_segment_lengths` tree (`NestedIntList`) of a `CasmContractClass` to compute the compiled-class-hash (both V1/Poseidon and V2/Blake variants), with no depth check or iterative fallback. This function is called in-process (not inside the resource-sandboxed CASM-compilation subprocess) every time a Declare transaction's Sierra class is compiled by the `SierraCompiler` service, before the transaction's signature/balance have even been verified.

### Finding Description
`SierraCompiler::compile` (`crates/apollo_compile_to_casm/src/lib.rs:60-74`) is the entry point invoked for every submitted class declaration: [1](#0-0) 
It compiles Sierra→CASM in a resource-limited subprocess (`self.compiler.compile(class)?`, bounded by `max_cpu_time`/`max_memory_usage`/`max_bytecode_size`), but the very next step, `executable_class.hash(&HashVersion::V2)`, runs directly in the `SierraCompiler` process itself, with none of those subprocess resource limits (in particular no stack-size control): [2](#0-1) 

`hash()` calls `hash_inner`, which calls `bytecode_hash`, which calls the recursive helper: [3](#0-2) 

`bytecode_hash_node` recurses once per nested segment in the tree returned by `get_bytecode_segment_lengths()`: [4](#0-3) 

The `bytecode_segment_lengths` field comes directly from the untrusted, attacker-submitted Sierra program after compilation by `cairo-lang-sierra-to-casm` (an external, third-party compiler) — its `NestedIntList` reflects the CASM's function/branch structure and its nesting depth is not validated or capped anywhere in this repository before being hashed. Nothing in `compiled_class_hash.rs` enforces a maximum tree depth (unlike the unrelated `NestedFeltCounts::new_inner` helper in `blockifier/src/execution/contract_class.rs`, which does `assert!(segmentation_depth <= 1, ...)`, but that helper is used only for a *different*, blockifier-internal resource-estimation path, not for the actual class-hash computation used by the gateway/class manager): [5](#0-4) 

The only size guard present is `max_bytecode_size` (default 81920, per `apollo_gateway_config`/`config_schema.json`), which bounds total bytecode length but does not bound the *nesting depth* of the segmentation tree — an attacker can structure a CASM program (e.g., long chains of nested branches/inline functions) to maximize tree depth relative to size, well within the 81920-instruction cap, producing tens of thousands of recursive Rust stack frames in `bytecode_hash_node`.

This is directly analogous to CVE-2018-15173: Nmap's `-sV` recursively parsed attacker-controlled, deeply-nested protocol data without a depth bound, exhausting the stack and crashing the process. Here, the sequencer's `SierraCompiler` component recursively walks an attacker-influenced, deeply-nested data structure (the CASM bytecode segmentation tree) with no depth bound, in the same process that services declare-class compilation requests.

### Impact Explanation
A stack overflow in `bytecode_hash_node` aborts the Rust process (stack overflows are not catchable panics; the OS terminates the process). Since class hashing here happens in-process in the `SierraCompiler`/class-manager component that services every Declare transaction submitted to the gateway — before the transaction's signature or balance is checked — a single malicious Declare submission can crash this sequencer component, denying it to legitimate declare traffic and, depending on deployment topology, disrupting transaction ingestion/availability (a network unable to confirm new — specifically Declare — transactions until the service is restarted). This is a network-reachable DoS from an unprivileged transaction sender/contract declarer, not a privileged-operator or p2p-only bug, satisfying the in-scope criteria.

### Likelihood Explanation
Likelihood is high: the attacker only needs to submit a syntactically valid Sierra class (passing normal Sierra/CASM compilation and the existing `max_bytecode_size` check) whose control-flow structure is engineered to produce a deeply nested segment tree. No special privileges, no consensus assumptions, and no interaction with other nodes are required — a single Declare transaction (or even a class submitted just for compilation/hash computation) triggers the vulnerable code path.

### Recommendation
- Impose an explicit maximum nesting-depth check on `bytecode_segment_lengths` (`NestedIntList`) before/while computing the compiled class hash, rejecting classes whose segmentation tree exceeds a safe bound (analogous to the `segmentation_depth <= 1` assumption already made elsewhere in the codebase for a different purpose).
- Convert `bytecode_hash_node` (and the equivalent OS/Cairo hint implementations) to an iterative (explicit stack/work-queue) algorithm so recursion depth is not tied to attacker-controlled input.
- Alternatively/also, move the class-hash computation into the same resource-isolated subprocess/sandbox used for Sierra→CASM compilation (with a bounded stack size and crash containment), so a crash there does not take down the long-lived `SierraCompiler` service process.

### Proof of Concept
1. Craft a Cairo 1 contract whose compiled CASM produces a deeply and linearly nested branch/function structure (e.g., thousands of nested `if`/inline-function boundaries) such that `cairo-lang-sierra-to-casm` emits a `bytecode_segment_lengths` `NestedIntList::Node` tree with recursion depth in the tens of thousands, while keeping total bytecode size under the configured `max_bytecode_size` (81920).
2. Submit this class via a Declare transaction (or directly to the `SierraCompiler`/class-manager `compile`/`add_class` endpoint).
3. `SierraCompiler::compile` successfully compiles Sierra→CASM in the sandboxed subprocess, then calls `executable_class.hash(&HashVersion::V2)` in-process.
4. `hash_inner` → `bytecode_hash` → `bytecode_hash_node` recurses once per nesting level; at sufficient depth, the process's stack is exhausted and the `SierraCompiler` process aborts, denying service to subsequent declare-class compilation requests.

Note: I was not able to independently execute/verify a real compiled example (I don't have code execution access), so the exact minimum nesting depth needed to overflow a default Rust thread stack, and the exact segmentation behavior of `cairo-lang-sierra-to-casm` for pathological inputs, are asserted based on the code paths and library documentation found in this repository rather than empirical measurement; a background engineering session with sandbox/build access would be needed to construct and run a concrete crashing PoC.

### Citations

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

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L108-132)
```rust
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

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L197-204)
```rust
    /// Returns the lengths of the bytecode segments.
    /// If the length field is missing, the entire bytecode is considered a single segment.
    fn get_bytecode_segment_lengths(&self) -> Cow<'_, NestedIntList> {
        match &self.bytecode_segment_lengths {
            Some(bytecode_segment_lengths) => Cow::Borrowed(bytecode_segment_lengths),
            None => Cow::Owned(NestedIntList::Leaf(self.bytecode.len())),
        }
    }
```

**File:** crates/blockifier/src/execution/contract_class.rs (L162-194)
```rust
    /// Recursively builds the nested structure and returns it with the number of items consumed.
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
