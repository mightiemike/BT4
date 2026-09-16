### Title
Unbounded recursion in `bytecode_hash_node` during compiled-class hashing allows a declared Sierra class to crash the sequencer via stack overflow - (File: crates/starknet_api/src/contract_class/compiled_class_hash.rs)

### Summary
`bytecode_hash_node` recursively walks the `bytecode_segment_lengths` tree (`NestedIntList`) of a compiled (CASM) class with no depth limit. This tree is derived from the Sierra program a user submits in a `DECLARE` transaction, compiled by the sequencer's own Sierra→CASM compiler. A crafted Sierra program that produces a deeply nested segmentation tree can drive this recursive function to a stack overflow while the sequencer is computing the compiled-class hash, matching the CVE-2020-18392 bug class (unbounded recursive-descent parsing/processing of attacker-supplied nested structures causing DoS).

### Finding Description
`SierraCompiler::compile` (crates/apollo_compile_to_casm/src/lib.rs) takes a user-submitted `SierraContractClass`, compiles it to CASM, and then calls `executable_class.hash(&HashVersion::V2)`: [1](#0-0) 

`HashableCompiledClass::hash` dispatches to `hash_inner`, which calls `bytecode_hash`, which calls `bytecode_hash_node` recursively over `NL: HashableNestedIntList` (concretely `NestedIntList` from `cairo_lang_starknet_classes`): [2](#0-1) 

`bytecode_hash_node` has **no depth check or recursion-depth guard**: for every `Node` it recurses once per child, and children can themselves be `Node`s, so the recursion depth equals the nesting depth of `bytecode_segment_lengths`, which is produced by the compiler from the structure of the declared Sierra program (its function/branch/segment layout): [3](#0-2) 

This is unlike the sibling helper `NestedFeltCounts::new_inner` in blockifier, which explicitly asserts a depth bound (`segmentation_depth <= 1`) — showing the codebase is aware such structures need depth limiting, but `bytecode_hash_node` lacks any such guard: [4](#0-3) 

The same unguarded recursive pattern also exists in `create_bytecode_segment_structure_inner` (Starknet OS hint implementation), which processes the identical `NestedIntList` structure during OS re-execution: [5](#0-4) 

Both are reachable from a plain `DECLARE` transaction: the gateway/compiler service compiles the Sierra program and computes its hash as part of normal transaction processing (`compile()` → `hash()`), and later the Starknet OS re-executes/validates the same compiled class using the same nested structure. An attacker who crafts a Sierra program whose compiled bytecode segmentation is deeply nested (e.g. via a long chain of deeply nested function definitions/branches within the maximum allowed Sierra program size) can push the recursion depth high enough to overflow the thread stack.

### Impact Explanation
A stack overflow in a sequencer worker thread while compiling/hashing a declared class (or in the OS while re-executing/proving the block containing the declare) crashes the process (Rust aborts on stack overflow; there is no catchable error, unlike the gas-bounded VM recursion case covered by `RUST_MIN_STACK`/`RecursionDepthGuard` for Cairo execution). This is a Denial-of-Service: a single malicious `DECLARE` transaction can crash the compilation service / sequencer node processing it, and if the class is included in a block, every node (or the OS prover) that re-executes/re-hashes it can crash identically, potentially halting block production/confirmation — i.e., "a network unable to confirm new transactions."

### Likelihood Explanation
Reachable directly from an unprivileged declarer: any account can submit a `DECLARE` transaction with an arbitrary Sierra program (subject only to a maximum program size, not a maximum segmentation-tree depth). Whether an attacker can actually force sufficiently deep segmentation nesting depends on the segmentation algorithm of the (externally maintained) `cairo_lang_starknet_classes` compiler, which is not fully verified from this codebase alone — the exact nesting depth achievable within the size limit is uncertain without testing against the compiler. Given the lack of any depth bound in `bytecode_hash_node`/`create_bytecode_segment_structure_inner`, and the precedent that this codebase does add depth guards for other identical structures (`NestedFeltCounts::new_inner`), the missing guard here is a genuine gap, but confirming exploitability requires generating an actual Sierra program with deep segmentation and reproducing a stack overflow, which was not verified in this analysis.

### Recommendation
Add an explicit, enforced maximum recursion/nesting depth check in `bytecode_hash_node` (and in `create_bytecode_segment_structure_inner`) that rejects/aborts compilation or hashing with a normal error when `bytecode_segment_lengths` exceeds a safe depth (mirroring the `assert!(segmentation_depth <= 1, ...)` pattern already used in `NestedFeltCounts::new_inner`), and validate this bound as part of stateless class validation before expensive compilation/hashing occurs.

### Proof of Concept
Not independently reproduced. Conceptually: craft a Sierra program (within `max_sierra_program_size`) whose Sierra→CASM compilation yields a `bytecode_segment_lengths: NestedIntList` with maximal nesting depth (e.g. via many nested function scopes/branches), submit it via a `DECLARE` v3 transaction, and observe whether `SierraCompiler::compile` (`crates/apollo_compile_to_casm/src/lib.rs:60-74`) crashes with a stack overflow when calling `.hash(&HashVersion::V2)`. Confirming this requires access to the actual segmentation output of `cairo_lang_starknet_classes` for deeply-nested Sierra input, which was not available in this analysis.

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

**File:** crates/blockifier/src/execution/contract_class.rs (L162-168)
```rust
    /// Recursively builds the nested structure and returns it with the number of items consumed.
    fn new_inner(
        bytecode_segment_lengths: &NestedIntList,
        bytecode: &[BigUintAsHex],
        segmentation_depth: usize,
    ) -> (Self, usize) {
        assert!(segmentation_depth <= 1, "Only supported for segmentation depth at most 1.");
```

**File:** crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs (L277-307)
```rust
pub(crate) fn create_bytecode_segment_structure_inner(
    bytecode: &[Felt],
    bytecode_segment_lengths: NestedIntList,
    bytecode_offset: usize,
) -> (BytecodeSegmentNode, usize) {
    match bytecode_segment_lengths {
        NestedIntList::Leaf(length) => {
            let segment_end = bytecode_offset + length;
            let bytecode_segment = bytecode[bytecode_offset..segment_end].to_vec();

            (BytecodeSegmentNode::Leaf(BytecodeSegmentLeaf { data: bytecode_segment }), length)
        }
        NestedIntList::Node(lengths) => {
            let mut segments = vec![];
            let mut total_len = 0;
            let mut bytecode_offset = bytecode_offset;

            for item in lengths {
                let (current_structure, item_len) =
                    create_bytecode_segment_structure_inner(bytecode, item, bytecode_offset);

                segments.push(BytecodeSegment { length: item_len, node: current_structure });

                bytecode_offset += item_len;
                total_len += item_len;
            }

            (BytecodeSegmentNode::InnerNode(BytecodeSegmentInnerNode { segments }), total_len)
        }
    }
}
```
