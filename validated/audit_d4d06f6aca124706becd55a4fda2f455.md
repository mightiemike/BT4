## Finding

### Title
Execution-backend-dependent revert-reason text is hashed into the consensus-critical `receipt_commitment`, risking honest-node state divergence - (File: crates/starknet_api/src/block_hash/receipt_commitment.rs)

### Summary
The Cosmos PFM advisory (GHSA-w6rp-vxj2-fjhr) describes a chain-halt caused by error text/state that is not guaranteed to be identical across validators being folded into consensus-relevant state, so honest nodes compute different results for the same input. The Starknet sequencer has an analogous mechanism: the human-readable revert-reason string produced during transaction execution is hashed directly into `receipt_commitment`, which feeds the block hash, and that string's exact contents depend on which execution backend (Cairo Native vs. Cairo VM/CASM) is running - a choice each node operator makes independently via `cairo_native_mode` config.

### Finding Description
`calculate_receipt_hash` in `crates/starknet_api/src/block_hash/receipt_commitment.rs:45-53` chains `get_revert_reason_hash`, which is `starknet_keccak_hash(reason.revert_reason.as_bytes())` [1](#0-0) . This means the exact bytes of the revert string are consensus-critical: any two nodes that produce a different string for the "same" logical failure will compute a different receipt hash, and therefore a different committed block hash.

That revert string is produced by `gen_tx_execution_error_trace` in `crates/blockifier/src/execution/stack_trace.rs`, which walks the internal error type and, depending on `TrackedResource` and `strip_vm_frames_in_sierra_gas`, either strips or includes low-level VM frames (`VmExceptionFrame` with `pc`/`traceback`) [2](#0-1) . The codebase itself documents that this text must be "byte-identical regardless of execution backend" to keep `receipt_commitment` invariant, and adds a regression test enforcing this for one specific scenario (a constructor revert under the "strip policy on" versioned-constants flag) [3](#0-2) .

Crucially, `cairo_native_mode` is a per-node configuration setting (`off`, `wait_on_compilation`, `lazy_compilation`) [4](#0-3) , so it is expected and supported for different validators/sequencers on the same network to run different execution backends for the same class hash. The single regression test only pins down backend-invariance for the narrow "constructor revert, SierraGas, strip-policy-on" path; it does not cover every divergent internal code path that can surface in a revert string (e.g. raw VM-internal `VirtualMachineError` `Display` text such as `"Couldn't compute operand op0. Unknown value for memory cell 1:23"` [5](#0-4) , `NativeUnrecoverableError` paths that only exist under the `cairo_native` feature [6](#0-5) , or arbitrary top-level "unrelated to Cairo execution" errors formatted via `error.to_string()` [7](#0-6) ).

### Impact Explanation
If any code path produces a revert string that differs between a Cairo-Native-enabled node and a CASM/VM-only node (or between different Native/CASM versions) for the same transaction, those nodes will independently compute different `receipt_commitment` values and thus different block hashes for an otherwise-identical block. This is a classic "wrong committed root or block hash / honest-node divergence" outcome — the same bug class as the Cosmos PFM chain-halt, where non-deterministic error data committed to state caused validators to diverge and halted the chain. A single attacker-submitted transaction that triggers a backend-sensitive failure mode is sufficient to trigger the divergence, since revert text is derived purely from how the network processes that one transaction.

### Likelihood Explanation
Medium. The developers were clearly aware of this risk class (as shown by the explicit `test_revert_text_is_backend_invariant_for_sierra_gas` test and its comments about `receipt_commitment` invariance), and have mitigated the specific scenario they tested. However, `cairo_native_mode` is an independent per-node config, meaning backend heterogeneity across validators is a real, supported deployment configuration rather than a hypothetical, and the invariance guarantee is only verified for one narrow code path rather than exhaustively for all failure modes (VM-internal errors, native-only error variants, generic top-level errors).

### Recommendation
- Exhaustively enumerate and test backend-invariance of revert-reason text for all `TransactionExecutionError`/`EntryPointExecutionError` variants that can reach `gen_tx_execution_error_trace`, not just the constructor/SierraGas case.
- Consider removing raw VM-internal details (memory cell references, tracebacks, native-only error text) from the string that is hashed into `receipt_commitment`, replacing them with a normalized, backend-agnostic error code/summary.
- Add fuzz/differential testing that runs the same transactions under both Cairo Native and CASM VM and asserts identical `revert_error` strings (and therefore identical `receipt_commitment`) for all failure classes, not only reverts.

### Proof of Concept
Not applicable as a standalone runnable PoC — this is a code-path analysis. A concrete PoC would involve: (1) constructing a Cairo1 contract call that fails via a code path not covered by the existing invariance test (e.g. one hitting `NativeUnrecoverableError` under Native vs. a `CairoRunError`/`VirtualMachineError` under CASM for the same logical failure), (2) executing it against two block-builder configurations, one with `cairo_native_mode = wait_on_compilation` and one with `off`, and (3) comparing the resulting `revert_error` strings and `receipt_commitment` values as computed in `crates/starknet_api/src/block_hash/receipt_commitment.rs`.

### Citations

**File:** crates/starknet_api/src/block_hash/receipt_commitment.rs (L71-78)
```rust
// Returns starknet-keccak of the revert reason ASCII string, or 0 if the transaction succeeded.
fn get_revert_reason_hash(execution_status: &TransactionExecutionStatus) -> Felt {
    match execution_status {
        TransactionExecutionStatus::Succeeded => Felt::ZERO,
        TransactionExecutionStatus::Reverted(reason) => {
            starknet_keccak_hash(reason.revert_reason.as_bytes())
        }
    }
```

**File:** crates/blockifier/src/execution/stack_trace.rs (L480-486)
```rust
        _ => {
            // Top-level error is unrelated to Cairo execution, no "real" frames.
            let mut stack = ErrorStack::default();
            stack.push(ErrorStackSegment::StringFrame(error.to_string()));
            stack
        }
    }
```

**File:** crates/blockifier/src/execution/stack_trace.rs (L735-754)
```rust
fn extract_entry_point_execution_error_into_stack_trace(
    error_stack: &mut ErrorStack,
    depth: usize,
    entry_point_error: &AnnotatedEntryPointExecutionError,
) {
    let inner = entry_point_error.unannotated();
    match inner {
        EntryPointExecutionError::CairoRunError(cairo_run_error) => {
            // Omit the cairo-vm PC/traceback only for SierraGas frames at versions where the
            // strip policy is on — makes the revert reason invariant across execution
            // backends. Cairo 0 (CairoSteps) always emits; it has no native counterpart.
            let omit_vm_frame = entry_point_error.strip_vm_frames_in_sierra_gas()
                && entry_point_error.tracked_resource() == TrackedResource::SierraGas;
            extract_cairo_run_error_into_stack_trace(
                error_stack,
                depth,
                cairo_run_error,
                omit_vm_frame,
            )
        }
```

**File:** crates/blockifier/src/execution/stack_trace.rs (L755-758)
```rust
        #[cfg(feature = "cairo_native")]
        EntryPointExecutionError::NativeUnrecoverableError(error) => {
            extract_syscall_execution_error_into_stack_trace(error_stack, depth, error)
        }
```

**File:** crates/blockifier/src/execution/stack_trace_test.rs (L891-918)
```rust
/// At v0.14.3+ (strip policy on), the same Cairo 1 flow must produce a byte-identical revert
/// string regardless of execution backend — this is what makes `receipt_commitment` invariant
/// under cairo-native vs cairo-vm CASM. Pre-patch (origin/main-v0.14.3), this assertion would
/// fail: CASM emitted `Error at pc=0:443:` / `Error at pc=0:797:` lines under the outer two
/// frames that native never produced, see the historical diff of
/// `test_contract_ctor_frame_stack_trace_cairo1_casm.txt`.
#[cfg(feature = "cairo_native")]
#[rstest]
fn test_revert_text_is_backend_invariant_for_sierra_gas(
    block_context: BlockContext,
    default_all_resource_bounds: ValidResourceBounds,
) {
    let casm_revert = render_faulty_ctor_revert(
        &block_context,
        default_all_resource_bounds,
        RunnableCairo1::Casm,
    );
    let native_revert = render_faulty_ctor_revert(
        &block_context,
        default_all_resource_bounds,
        RunnableCairo1::Native,
    );
    assert_eq!(
        casm_revert, native_revert,
        "Cairo 1 revert text must be backend-invariant at v0.14.3+; CASM and Native \
         diverged.\nCASM:\n{casm_revert}\n\nNative:\n{native_revert}"
    );
}
```

**File:** crates/apollo_node/resources/config_schema.json (L247-251)
```json
  "batcher_config.static_config.contract_class_manager_config.cairo_native_run_config.cairo_native_mode": {
    "description": "Cairo native execution mode. 'off' disables native execution, 'wait_on_compilation' compiles synchronously, and 'lazy_compilation' compiles asynchronously.",
    "privacy": "Public",
    "value": "off"
  },
```

**File:** crates/blockifier/src/execution/stack_trace_regression/test_trace_call_chain_with_syscalls_cairo0_invoke_call_chain_call.txt (L20-25)
```text
3: Error in the called contract (contract address: 0x0000000000000000000000000000000000000000000000000000000040070000, class hash: 0x0000000000000000000000000000000000000000000000000000000000070000, selector: 0x0062c83572d28cb834a3de3c1e94977a4191469a4a8c26d1d7bc55305e640ed5):
Error at pc=0:1928:
Cairo traceback (most recent call last):
Unknown location (pc=0:2013)

Couldn't compute operand op0. Unknown value for memory cell 1:23
```
