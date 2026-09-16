## Title
Sequencer panic via unbounded/mis-assumed bytecode-segment nesting depth in `CasmContractClass` conversion - (File: `crates/blockifier/src/execution/contract_class.rs`)

## Summary
The DNS dissector bug (CVE-2017-9345) is a class of vulnerability where a parser recurses/loops over an attacker-influenced nested structure without validating the structure's shape, leading to an unhandled condition (infinite loop / crash). The sequencer has an analogous weakness in `NestedFeltCounts::new_inner`, which recursively walks the `bytecode_segment_lengths: NestedIntList` field of a declared class's `CasmContractClass` but hard-codes an assumption that this structure never nests deeper than one level, enforced via a runtime `assert!` rather than graceful validation/rejection.

## Finding Description
`NestedFeltCounts::new_inner` recursively descends into the `NestedIntList` describing how a contract's bytecode is segmented: [1](#0-0) 

Note the hard assumption at line 168: `assert!(segmentation_depth <= 1, "Only supported for segmentation depth at most 1.");`. This is invoked from `TryFrom<VersionedCasm> for CompiledClassV1`, the routine used to convert a compiled class into the in-memory representation used by the blockifier before/while executing a call to that class: [2](#0-1) 

`bytecode_segment_lengths` originates from `get_bytecode_segment_lengths()` on the `CasmContractClass`, which is populated by compiling the declarer-supplied Sierra program: [3](#0-2) . The Sierra→CASM compiler's segmentation structure is a nested tree that can legitimately (and can be deliberately engineered by a declarer) exceed one level of nesting — e.g., a function containing nested branches, or nested function calls each producing their own segment. Unlike `create_bytecode_segment_structure_inner` (used elsewhere for the same purpose, and correctly unbounded/depth-agnostic): [4](#0-3) , the blockifier's `NestedFeltCounts::new_inner` panics instead of handling depth > 1.

Because class conversion happens during contract-class loading for execution (called from multiple state-reader paths across the sequencer, gateway, and RPC-execution crates), any transaction that invokes a declared class whose compiled bytecode segmentation has nesting depth greater than 1 will cause every node executing/re-executing that transaction to hit the `assert!` and panic.

## Impact Explanation
A panic during contract-class loading, triggered deterministically by the structure of a declared/invoked class, crashes the executing process. Since this code path runs on every sequencer/full node that executes or re-executes the transaction (block building, mempool validation and consensus re-execution, RPC execution, Starknet OS re-execution equivalents), a single crafted declare + invoke sequence can be used to deterministically crash sequencer/validator nodes processing that block, producing a chain-wide denial of service — nodes are unable to confirm new transactions until manually patched/restarted, satisfying the "network unable to confirm new transactions" impact bar.

## Likelihood Explanation
Reachable directly from an unprivileged account: any account can submit a `DECLARE` transaction with a Sierra program engineered (through nested function/branch structure) to compile into a `CasmContractClass` whose `bytecode_segment_lengths` nests more than one level deep, then submit an `INVOKE` calling that class. This requires no special privileges, only standard gas/fee payment, and depends only on the shape of the compiled bytecode segmentation tree, which is influenced by ordinary Sierra-to-CASM compilation of a contract with sufficiently nested code structure.

## Recommendation
Replace the `assert!(segmentation_depth <= 1, ...)` in `NestedFeltCounts::new_inner` (`crates/blockifier/src/execution/contract_class.rs`) with proper unbounded (or explicitly depth-limited-with-graceful-rejection) recursion handling, mirroring the approach already used in `create_bytecode_segment_structure_inner`. If depth is truly expected to be bounded by protocol rules, validate and reject non-conforming classes during stateless/stateful class declaration validation (gateway) rather than panicking deep inside execution/state-reading code paths.

## Proof of Concept
1. Craft a Sierra contract whose compiled CASM naturally produces a `bytecode_segment_lengths` tree with nesting depth ≥ 2 (e.g., a function containing an inlined function with its own internal branch segmentation), which is well within the capabilities of the standard Sierra→CASM compiler for a moderately complex contract with nested conditional/function structures.
2. Submit a `DECLARE` transaction for this class from any funded account.
3. Submit an `INVOKE` transaction calling into this class (any entry point) so that a sequencer node must load/convert the `CasmContractClass` via `TryFrom<VersionedCasm> for CompiledClassV1`.
4. Observe that `NestedFeltCounts::new_inner`'s `assert!(segmentation_depth <= 1, ...)` fires, panicking the executing node process.

### Citations

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

**File:** crates/blockifier/src/execution/contract_class.rs (L666-677)
```rust

        let bytecode_segment_felt_sizes =
            NestedFeltCounts::new(&class.get_bytecode_segment_lengths(), &class.bytecode);

        Ok(CompiledClassV1(Arc::new(ContractClassV1Inner {
            program,
            entry_points_by_type: (&class.entry_points_by_type).into(),
            hints: string_to_hint,
            sierra_version,
            bytecode_segment_felt_sizes,
        })))
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
