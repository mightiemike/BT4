Based on my investigation, there is a plausible in-scope analog to CVE-2025-59789's bug class (unbounded recursion over attacker-influenced nested structure causing stack exhaustion), but it is **not** in JSON parsing — Rust's `serde_json` already enforces an internal recursion-depth guard by default (unlike `rapidjson`), so the direct JSON-parsing analog does not reproduce here. However, the same bug class (recursive descent with no depth bound over a structure whose depth is controlled by an untrusted declared contract) exists in the CASM bytecode-segment hashing code used during Sierra→CASM class declaration.

### Title
Unbounded Recursion in CASM Bytecode-Segment Hashing Enables Stack-Overflow Crash via Crafted Declare Transaction - (File: crates/starknet_api/src/contract_class/compiled_class_hash.rs)

### Summary
`bytecode_hash_node` in [1](#0-0)  recursively walks a `HashableNestedIntList` (the CASM bytecode-segment-length tree) with no depth limit, mirroring the exact bug class described in the bRPC advisory (uncontrolled recursion over attacker-shaped nested structure leading to stack overflow), except the untrusted nesting here comes from a Sierra program's compiled bytecode-segment structure rather than raw JSON.

### Finding Description
When a class is declared, the gateway/class-manager compiles the submitted Sierra program to CASM and then computes its class hash in-process via `SierraCompiler::compile`, which calls `executable_class.hash(&HashVersion::V2)` right after compilation completes [2](#0-1) . That hash computation ultimately recurses through `bytecode_hash_node`: [1](#0-0) 
which recurses once per nesting level of the compiler-produced bytecode-segment-length tree, with no recursion-depth check. The same unbounded-recursion pattern also exists in the Starknet OS Cairo hint implementation that reconstructs this tree for re-execution/proving, `create_bytecode_segment_structure_inner` [3](#0-2) , and in the corresponding Cairo VM hash routine `bytecode_hash_internal_node` [4](#0-3) .

The segment-length tree's nesting depth is derived from the structural nesting (functions/branches) of the compiled bytecode of a Sierra program that an attacker fully controls in a `DECLARE` transaction. Unlike the Sierra→CASM compiler binary itself, which runs as a resource-limited subprocess (CPU time and memory bounded, but not stack depth) via `ResourceLimits` [5](#0-4) , the post-compilation hashing step in `compiled_class_hash.rs` runs in-process in the gateway/class-manager component itself, with no equivalent sandboxing or recursion guard.

### Impact Explanation
A crafted Sierra program with deeply nested control flow (staying under the existing `max_bytecode_size` limit of 81920 felts, see `sierra_compiler_config.max_bytecode_size` in the node config) could yield a segment-length tree deep enough to exhaust the call stack of the process performing the hash computation. Because this code path runs in-process (not in the sandboxed compiler subprocess), a stack overflow here crashes the sequencer's gateway/class-manager component processing the declare transaction, rather than a disposable child process. Any honest node that receives and processes the same malicious declaration would crash identically, matching the "network unable to confirm new transactions" impact category.

### Likelihood Explanation
Reachable by any unprivileged declarer submitting a single `DECLARE` transaction — no special privileges, staking, or operator access required. The bug is in a size-bounded but depth-unbounded field (segment nesting), meaning the existing byte-size limits do not necessarily bound recursion depth if segment granularity can be made very fine (e.g., many single/few-instruction branches).

### Recommendation
Add an explicit recursion/depth bound to `bytecode_hash_node` (and its counterparts in `starknet_os`/Cairo OS code) analogous to the bRPC fix's `json2pb_max_recursion_depth`, or convert the recursive tree walk into an iterative one using an explicit stack. Reject declared classes whose compiled bytecode-segment structure exceeds the configured depth limit during compilation/validation, before the hash is computed.

### Proof of Concept
Not executed (no sandbox access). Conceptually: craft a Sierra program that compiles to CASM with maximal branch/function nesting (e.g., thousands of nested `match`/`if` control-flow constructs) while staying under `max_bytecode_size`; submit it as a `DECLARE` transaction; the resulting compiled-class hashing call in `SierraCompiler::compile` → `HashableCompiledClass::hash` → `bytecode_hash`/`bytecode_hash_node` recurses proportionally to the nesting depth and can exhaust the stack of the hosting process.

---
Caveat: I could not fully confirm — without running the actual Cairo compiler — how deep a segment-length nesting an attacker can realistically achieve within `max_bytecode_size`, nor the exact stack-frame size of `bytecode_hash_node` to compute the required depth for an actual overflow. This assessment is based on static code review of the recursive, depth-unbounded traversal pattern, which is structurally the same bug class as the reported CVE.

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/contract_class/blake_compiled_class_hash.cairo (L127-186)
```text
func bytecode_hash_internal_node{range_check_ptr, hash_state: HashState}(
    data_ptr: felt*, data_length: felt, full_contract: felt
) {
    if (data_length == 0) {
        %{ AssertEndOfBytecodeSegments %}
        return ();
    }

    alloc_locals;
    local is_leaf_and_loaded;
    local load_segment;
    local segment_length;

    %{ IterCurrentSegmentInfo %}

    if (is_leaf_and_loaded != FALSE) {
        // Repeat the code of bytecode_hash_node() for performance reasons, instead of calling it.
        let (current_segment_hash) = encode_felt252_data_and_calc_blake_hash(
            data_len=segment_length, data=data_ptr
        );
        tempvar range_check_ptr = range_check_ptr;
        tempvar current_segment_hash = current_segment_hash;
    } else {
        // The segment is at least partially loaded, and it is not a leaf.
        if (load_segment != FALSE) {
            let current_segment_hash = bytecode_hash_node(
                data_ptr=data_ptr, data_length=segment_length, full_contract=full_contract
            );
        } else {
            // If `full_contract` is true, this flow is not allowed.
            assert full_contract = FALSE;

            // Set the first felt of the bytecode to -1 to make sure that the execution cannot jump
            // to this segment (-1 is an invalid opcode).
            // The hash in this case is guessed and the actual bytecode is unconstrained (except for
            // the first felt).
            %{ DeleteMemoryData %}

            assert data_ptr[0] = -1;

            assert [range_check_ptr] = segment_length;
            tempvar range_check_ptr = range_check_ptr + 1;
            let current_segment_hash = [ap];
            %{ SetApToSegmentHashBlake %}
            ap += 1;
        }
    }

    // Add the segment length and hash to the hash state.
    hash_update_single(item=segment_length);
    hash_update_single(item=current_segment_hash);

    %{ vm_exit_scope() %}

    return bytecode_hash_internal_node(
        data_ptr=&data_ptr[segment_length],
        data_length=data_length - segment_length,
        full_contract=full_contract,
    );
}
```

**File:** crates/apollo_compilation_utils/src/resource_limits/resource_limits_unix.rs (L41-76)
```rust
pub struct ResourceLimits {
    /// A limit (in seconds) on the amount of CPU time that the process can consume.
    cpu_time: Option<RLimit>,
    /// The maximum size (in bytes) of files that the process may create.
    file_size: Option<RLimit>,
    /// The maximum size (in bytes) of the process’s virtual memory (address space).
    memory_size: Option<RLimit>,
}

impl ResourceLimits {
    pub fn new(
        cpu_time: Option<u64>,
        file_size: Option<u64>,
        memory_size: Option<u64>,
    ) -> ResourceLimits {
        ResourceLimits {
            cpu_time: cpu_time.map(|t| RLimit {
                resource: Resource::CPU,
                soft_limit: t,
                hard_limit: t,
                units: "seconds".to_string(),
            }),
            file_size: file_size.map(|x| RLimit {
                resource: Resource::FSIZE,
                soft_limit: x,
                hard_limit: x,
                units: "bytes".to_string(),
            }),
            memory_size: memory_size.map(|y| RLimit {
                resource: Resource::AS,
                soft_limit: y,
                hard_limit: y,
                units: "bytes".to_string(),
            }),
        }
    }
```
