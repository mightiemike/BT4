### Title
`revert_reason` string included in the on-chain `receipt_commitment` is only backend-invariant by test convention, not by enforced invariant — divergence between Cairo-VM and Cairo-Native paths breaks state/root agreement - (File: `crates/blockifier/src/execution/stack_trace.rs`, `crates/starknet_api/src/block_hash/receipt_commitment.rs`)

### Summary
The reported vm2 bug class is: a sanitization routine that is supposed to normalize/strip sensitive or backend-specific content from an error object handles some known sub-cases explicitly (`SuppressedError`, `AggregateError`) but silently omits another reachable case (`Error.cause`), so the "sanitized" value that later flows into a security-critical decision leaks unsanitized state. The sequencer contains a structurally identical pattern: `extract_cairo_run_error_into_stack_trace` / `extract_entry_point_execution_error_into_stack_trace` in `crates/blockifier/src/execution/stack_trace.rs` is responsible for producing the exact `revert_reason` string that is later hashed into the block's committed `receipt_commitment` via `calculate_receipt_hash` in `crates/starknet_api/src/block_hash/receipt_commitment.rs:45-53`. The whole design explicitly depends on this string being **byte-identical** regardless of whether the transaction executed on Cairo-VM (CASM) or Cairo-Native, enforced today only by a single regression test (`test_revert_text_is_backend_invariant_for_sierra_gas`, `crates/blockifier/src/execution/stack_trace_test.rs:891-918`), not by a structural guarantee in the formatter code.

### Finding Description
`calculate_receipt_hash` folds `revert_reason` directly into the Poseidon hash chain that becomes each transaction's leaf in the receipt Patricia tree, which in turn is committed into the block header (`receipt_commitment`): [1](#0-0) 

Because `revert_reason` is part of consensus-critical, committed data, any two honest nodes that compute a different string for the same failing transaction will produce different `receipt_commitment` values and thus different block hashes — a chain split.

The revert string is produced by walking nested error types (`CairoRunError`, `VirtualMachineError`, `SyscallExecutionError`, `DeprecatedSyscallExecutionError`, native errors) and conditionally omitting VM-specific frames (`omit_vm_frame` / `strip_vm_frames_in_sierra_gas`) so that Cairo-VM and Cairo-Native executions of the same failing contract render identical text: [2](#0-1) 

This mirrors the vm2 defect exactly: the stripping logic enumerates specific known cases (`CairoRunError::VmException` → strip the VM frame when `omit_vm_frame` is set) and falls through to `error.to_string()` / `format!("{vm_error}\n")` for every other, unenumerated branch: [3](#0-2) [4](#0-3) 

Any new/rare error variant that is: (a) reachable through Cairo-Native's error path but not through Cairo-VM's (or vice versa), and (b) not covered by the fallback `_ => error_stack.push(syscall_error.to_string().into())`/`format!("{vm_error}\n")` branches with backend-identical `Display` output, will silently produce backend-divergent `revert_reason` text. Since backend selection (CASM vs Native) is itself decided per-node/per-config (`RunnableCairo1::Casm` vs `RunnableCairo1::Native`, feature-gated by `cairo_native`), two honest sequencer/full nodes running different backends (or the same node re-executing under Starknet OS with a different backend than the sequencer used) can diverge on the committed receipt data for an otherwise-identical execution outcome.

The equivalence is currently guarded by exactly one test asserting textual equality for one specific failure scenario (constructor revert): [5](#0-4) 

There is no exhaustive, type-level guarantee (e.g., an exhaustive match with a compile-time assertion, or a canonicalization step run over *all* error variants before hashing) — the same "cover known sub-cases, silently fall through for everything else" architecture that caused the vm2 CVE.

### Impact Explanation
If a code path exists (new syscall error variant, new native-only panic representation, a `HintError`/`VirtualMachineError` variant added for one backend but not mirrored/formatted identically for the other, etc.) where the rendered `revert_reason` differs between Cairo-VM and Cairo-Native execution of the same transaction, nodes computing the block using different backends will produce different `receipt_commitment` and hence different block hashes for the same block content. This is a direct **honest-node divergence / consensus split**: the network becomes unable to agree on the canonical block, which can halt block finalization or fork the chain — squarely inside the “honest-node divergence” and “network unable to confirm new transactions” impact categories explicitly accepted by the validation rules. This is reachable purely by an unprivileged transaction sender crafting a call that fails with the divergent error path (fee/resource-neutral, requires no special privilege) — i.e., "malicious contract deployer/caller triggers a specific revert type" is fully within the single-transaction-sender threat model.

### Likelihood Explanation
Likelihood is *moderate*: the invariant is actively maintained by StarkWare (the code comments and the dedicated regression test show they are aware of this exact risk class and test for at least one instance of it), so a fully-manifested divergence requires finding an untested error branch. However, the enumeration in `extract_syscall_execution_error_into_stack_trace` / `extract_deprecated_syscall_execution_error_into_stack_trace` explicitly matches only 4 variants each and falls back to `syscall_error.to_string()` for "everything else" — and native execution paths (`EntryPointExecutionError::NativeUnexpectedError`, `NativeUnrecoverableError`) sit alongside CASM-only paths (`CairoRunError`), so the two backends do not funnel through a single shared formatter for all error kinds. Any future addition of an error variant reachable only from one backend (a realistic occurrence given the codebase actively adds new syscalls/native error kinds) reintroduces the exact "we tested known sub-cases, forgot a new one" gap that caused the referenced CVE, without any compile-time or fuzz-time enforcement blocking it.

### Recommendation
1. Do not rely solely on a single regression test to enforce revert-text backend invariance. Add a structural safeguard: an exhaustive (`#[non_exhaustive]`-free, compiler-checked) match over every `EntryPointExecutionError`/`SyscallExecutionError`/`VirtualMachineError` variant in the stack-trace formatter, so any newly added variant forces the author to explicitly decide/verify backend-invariant rendering (mirroring the suggested vm2 fix of handling `.cause` unconditionally rather than via prototype-chain walks that must be manually kept in sync).
2. Add differential/fuzz testing that runs the same failing contract under both `RunnableCairo1::Casm` and `RunnableCairo1::Native` for every error-producing syscall and VM error kind (not just the one constructor-revert case), asserting `revert_reason` byte-equality before it is fed into `calculate_receipt_hash`.
3. Consider canonicalizing/normalizing the revert string via a single shared code path invoked identically by both backends, rather than two backend-specific traversal functions that must independently agree.

### Proof of Concept
Conceptual PoC (concrete divergent input requires enumerating the currently-untested error variants, which is not confirmed present at time of this analog — flagged as unverified):
1. Compile a Sierra contract that triggers an error type reachable only via one execution backend at a Sierra-gas-tracked (`TrackedResource::SierraGas`) call depth where `strip_vm_frames_in_sierra_gas` is expected to make output backend-invariant (see `crates/blockifier/src/execution/errors.rs` `annotated`/`strip_vm_frames_in_sierra_gas` field usage, and `crates/blockifier/src/execution/stack_trace_test.rs:891-918` for the existing test pattern).
2. Execute the transaction on one honest node configured to run Cairo-Native and another configured to run Cairo-VM (CASM) for the same class.
3. Compare `TransactionExecutionInfo::output_for_hashing()` (`crates/blockifier/src/transaction/objects.rs:283-306`) → `revert_reason` across both nodes; a mismatch propagates into `calculate_receipt_hash` (`crates/starknet_api/src/block_hash/receipt_commitment.rs:45-53`) and yields divergent `receipt_commitment`/block hashes.

Note: I could not confirm within available search results a *currently* existing, concretely triggerable error variant that breaks the invariant (the codebase's existing regression test suggests known cases are covered). This finding documents a structural/architectural weakness analogous to the reported CVE's root cause rather than a proven live exploit — a background engineer would need to enumerate all `EntryPointExecutionError`/`SyscallExecutionError`/native-error variants to determine if an untested divergent case currently exists.

### Citations

**File:** crates/starknet_api/src/block_hash/receipt_commitment.rs (L41-53)
```rust
// Poseidon(
//    transaction hash, amount of fee paid, hash of messages sent, revert reason,
//    execution resources
// ).
fn calculate_receipt_hash(receipt_element: &ReceiptElement) -> Felt {
    let hash_chain = HashChain::new()
        .chain(&receipt_element.transaction_hash)
        .chain(&receipt_element.transaction_output.actual_fee.0.into())
        .chain(&calculate_messages_sent_hash(&receipt_element.transaction_output.messages_sent))
        .chain(&get_revert_reason_hash(&receipt_element.transaction_output.execution_status));
    chain_gas_consumed(hash_chain, &receipt_element.transaction_output.gas_consumed)
        .get_poseidon_hash()
}
```

**File:** crates/blockifier/src/execution/stack_trace.rs (L514-535)
```rust
fn extract_cairo_run_error_into_stack_trace(
    error_stack: &mut ErrorStack,
    depth: usize,
    error: &CairoRunError,
    omit_vm_frame: bool,
) {
    if let CairoRunError::VmException(vm_exception) = error {
        if !omit_vm_frame {
            error_stack.push(
                VmExceptionFrame {
                    pc: vm_exception.pc,
                    error_attr_value: vm_exception.error_attr_value.clone(),
                    traceback: vm_exception.traceback.clone(),
                }
                .into(),
            );
        }
        extract_virtual_machine_error_into_stack_trace(error_stack, depth, &vm_exception.inner_exc);
    } else {
        error_stack.push(error.to_string().into());
    }
}
```

**File:** crates/blockifier/src/execution/stack_trace.rs (L537-577)
```rust
fn extract_virtual_machine_error_into_stack_trace(
    error_stack: &mut ErrorStack,
    depth: usize,
    vm_error: &VirtualMachineError,
) {
    match vm_error {
        VirtualMachineError::Hint(ref boxed_hint_error) => {
            if let HintError::Internal(internal_vm_error) = &boxed_hint_error.1 {
                return extract_virtual_machine_error_into_stack_trace(
                    error_stack,
                    depth,
                    internal_vm_error,
                );
            }
            error_stack.push(boxed_hint_error.1.to_string().into());
        }
        VirtualMachineError::Other(anyhow_error) => {
            let syscall_exec_err = anyhow_error.downcast_ref::<SyscallExecutionError>();
            if let Some(downcast_anyhow) = syscall_exec_err {
                extract_syscall_execution_error_into_stack_trace(
                    error_stack,
                    depth,
                    downcast_anyhow,
                )
            } else {
                let deprecated_syscall_exec_err =
                    anyhow_error.downcast_ref::<DeprecatedSyscallExecutionError>();
                if let Some(downcast_anyhow) = deprecated_syscall_exec_err {
                    extract_deprecated_syscall_execution_error_into_stack_trace(
                        error_stack,
                        depth,
                        downcast_anyhow,
                    )
                }
            }
        }
        _ => {
            error_stack.push(format!("{vm_error}\n").into());
        }
    }
}
```

**File:** crates/blockifier/src/execution/stack_trace.rs (L579-652)
```rust
fn extract_syscall_execution_error_into_stack_trace(
    error_stack: &mut ErrorStack,
    depth: usize,
    syscall_error: &SyscallExecutionError,
) {
    match syscall_error {
        SyscallExecutionError::CallContractExecutionError {
            class_hash,
            storage_address,
            selector,
            error,
        } => {
            error_stack.push(
                EntryPointErrorFrame {
                    depth,
                    preamble_type: PreambleType::CallContract,
                    storage_address: *storage_address,
                    class_hash: *class_hash,
                    selector: Some(*selector),
                }
                .into(),
            );
            extract_syscall_execution_error_into_stack_trace(error_stack, depth + 1, error)
        }
        SyscallExecutionError::LibraryCallExecutionError {
            class_hash,
            storage_address,
            selector,
            error,
        } => {
            error_stack.push(
                EntryPointErrorFrame {
                    depth,
                    preamble_type: PreambleType::LibraryCall,
                    storage_address: *storage_address,
                    class_hash: *class_hash,
                    selector: Some(*selector),
                }
                .into(),
            );
            extract_syscall_execution_error_into_stack_trace(error_stack, depth + 1, error);
        }
        SyscallExecutionError::ConstructorEntryPointExecutionError(
            ConstructorEntryPointExecutionError::ExecutionError {
                error,
                class_hash,
                contract_address,
                constructor_selector,
            },
        ) => {
            error_stack.push(
                EntryPointErrorFrame {
                    depth,
                    preamble_type: PreambleType::Constructor,
                    storage_address: *contract_address,
                    class_hash: *class_hash,
                    selector: *constructor_selector,
                }
                .into(),
            );
            extract_entry_point_execution_error_into_stack_trace(error_stack, depth, error)
        }
        SyscallExecutionError::EntryPointExecutionError(entry_point_error) => {
            extract_entry_point_execution_error_into_stack_trace(
                error_stack,
                depth,
                entry_point_error,
            )
        }
        _ => {
            error_stack.push(syscall_error.to_string().into());
        }
    }
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
