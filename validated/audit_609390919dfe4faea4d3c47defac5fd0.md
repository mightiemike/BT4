### Title
Attacker-controlled `bytecode_segment_lengths` in a declared CASM class cause an out-of-bounds panic / hash-integrity bypass during compiled-class-hash computation - (File: crates/starknet_api/src/contract_class/compiled_class_hash.rs)

### Summary
`CasmContractClass::bytecode_segment_lengths` is an attacker-supplied field of the CASM contract class submitted with a `DECLARE` transaction. It is consumed by `bytecode_hash_node` in `crates/starknet_api/src/contract_class/compiled_class_hash.rs` and by `create_bytecode_segment_structure_inner` in `crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs` without validating that each declared leaf/segment length is consistent with the remaining bytecode. This mirrors the ALPINE-CVE-2026-42495 bug class: lengths taken directly from attacker-controlled structured data are used to slice/iterate a buffer without bounds checks, causing under/overflow or out-of-bounds access.

### Finding Description
`bytecode_hash_node` recurses over `NestedIntList` segment-length metadata that comes straight from the declared CASM JSON (`bytecode_segment_lengths`), and for each leaf calls: [1](#0-0) 
`iter.take(len).collect_vec()` followed by `assert_eq!(data.len(), len)` — if a leaf's declared `len` exceeds the number of felts remaining in the shared iterator, `data.len() != len` and the process panics via `assert_eq!`. Because segment lengths are arbitrary nested integers fully controlled by the class declarer, a mismatched (too large) leaf length reliably triggers this assertion failure with no earlier validation step rejecting the malformed structure gracefully.

The same pattern recurs in the Starknet OS' Cairo-side hint helper, which performs unconstrained slicing based on the same untrusted field: [2](#0-1) 
`bytecode[bytecode_offset..segment_end]` with `segment_end = bytecode_offset + length` — if `length` (from `NestedIntList::Leaf`) is larger than the truly remaining bytecode, this indexing panics with an out-of-bounds slice error rather than returning a controlled `Result`.

Both of these are used to compute (or re-verify) the compiled class hash for CASM classes submitted at `DECLARE` time and consumed again by the OS during re-execution (`create_bytecode_segment_structures` hint / `bytecode_hash_node` for hash verification) — i.e. an unprivileged transaction sender (the class declarer) fully controls the input triggering the panic.

### Impact Explanation
A panic in the sequencer's compiled-class-hash computation or in the Starknet OS re-execution path (hint processing during proving) directly affects the ability to declare/execute Sierra classes and can crash the block-building/hash-computation process or cause divergent behavior between the sequencer and OS re-execution if one side panics while another gracefully rejects (or vice versa). This can lead to inability to process the transaction (denial-of-service on the declare path) and, in the OS-hint case, a crash mid-proving that stalls block finalization for the affected block — matching the "network unable to confirm new transactions" / freezing criterion.

### Likelihood Explanation
Likelihood is high for the mismatch condition to be reachable: `bytecode_segment_lengths` is a normal, user-supplied field in the CASM JSON structure attached to a `DECLARE` transaction (see the various `compiled_classes`/`bytecode_segment_lengths` occurrences in resource fixtures, e.g. `crates/apollo_consensus_orchestrator/resources/central_blob.json` and `crates/starknet_os_flow_tests/resources/data_gas_account.casm.json`), and nothing in the code paths reviewed performs an early bound check that a segment/leaf length cannot exceed the bytecode length before recursively consuming the shared iterator/slice. Any class declarer can supply a CASM class whose `bytecode_segment_lengths` sums/leaf sizes don't match `bytecode.len()`.

### Recommendation
- In `bytecode_hash_node` (`crates/starknet_api/src/contract_class/compiled_class_hash.rs`), replace the `assert_eq!(data.len(), len)` panic with a proper `Result`-based validation error that is surfaced to the gateway/blockifier as a rejected declare transaction (invalid compiled class), rather than panicking the process.
- In `create_bytecode_segment_structure_inner` (`crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs`), bound-check `bytecode_offset + length <= bytecode.len()` before slicing, returning `OsHintError` instead of indexing out of bounds.
- Add an explicit stateless validation step at gateway ingestion (similar to existing Sierra-version validation in `apollo_gateway/src/stateless_transaction_validator_test.rs`) that verifies the sum of all leaf lengths in `bytecode_segment_lengths` equals `bytecode.len()` and that no partial/intermediate mismatch occurs, before this data reaches hash computation.

### Proof of Concept
Craft a `DECLARE` transaction with a `CasmContractClass` whose `bytecode` has, e.g., 3 felts, but whose `bytecode_segment_lengths` is `NestedIntList::Node([NestedIntList::Leaf(5)])` (a single leaf claiming 5 felts). When `CompiledClassV1::try_from((casm_contract_class, sierra_version))` (via `crates/blockifier/src/execution/contract_class.rs`) or the compiled-class-hash routine invokes `bytecode_hash_node`, the iterator will yield only 3 elements for `iter.take(5)`, so `data.len() (3) != len (5)`, triggering the `assert_eq!` panic in `crates/starknet_api/src/contract_class/compiled_class_hash.rs` lines 116-120. The equivalent malformed structure fed into `create_bytecode_segment_structure_inner` during OS re-execution triggers a slice-index-out-of-bounds panic at `crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs` lines 283-287.

### Citations

**File:** crates/starknet_api/src/contract_class/compiled_class_hash.rs (L111-120)
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
```

**File:** crates/starknet_os/src/hints/hint_implementation/compiled_class/utils.rs (L277-306)
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
```
