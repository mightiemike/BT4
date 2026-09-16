### Title
Panic (DoS) in `secp256k1_add`/`secp256k1_mul`/`secp256r1_add`/`secp256r1_mul` syscalls when segment not yet initialized - ([File: crates/blockifier/src/execution/syscalls/syscall_executor.rs])

### Summary
The `secp256k1_add`, `secp256k1_mul`, `secp256r1_add`, and `secp256r1_mul` default syscall implementations call `.expect("Secp segment must be set.")` on an `Option` returned by `get_secpk1_hint_processor_and_base()` / `get_secpr1_hint_processor_and_base()`, assuming the underlying secp segment has already been initialized by a prior `secp*_new` call. [1](#0-0) [2](#0-1) [3](#0-2) 

In contrast, `secp256k1_new`, `secp256k1_get_point_from_x`, and `secp256k1_get_xy` pass the `Option` through to their handler functions without unwrapping, implying those operations are designed to tolerate (or lazily initialize) an unset segment. [4](#0-3) 

### Finding Description
This mirrors the reported TensorFlow bug class: a kernel/handler assumes an internal invariant (`id` being scalar, `batch_index` having the right shape) holds without validating it, and hits a hard `CHECK`/`expect` failure that aborts the process when that assumption is violated by attacker-supplied input. Here, the invariant is "the secp segment has already been created via `secp*_new` before `secp*_add`/`secp*_mul` is called." Any Cairo1 contract can invoke Starknet syscalls in arbitrary order chosen by its own code; nothing in the visible call chain prevents a contract from calling `secp256k1_add` or `secp256k1_mul` as the very first secp-related syscall in its execution, before any `secp256k1_new` call establishes `optional_secp_segment_base`.

### Impact Explanation
If `optional_secp_segment_base` is `None` at that point, the `.expect("Secp segment must be set.")` triggers a Rust panic during transaction execution inside the blockifier. Since transaction execution happens both when the sequencer builds a block and when other nodes/Starknet OS re-execute the same block, a single malicious transaction can crash the executing process on every honest node that processes it, which can produce a network unable to confirm new transactions (a chain-halting DoS), matching the required impact bar.

### Likelihood Explanation
Reaching this requires only deploying a simple Cairo1 contract that calls `secp256k1_add` (or the r1/mul variants) without a preceding `secp256k1_new`, then invoking it via a normal `INVOKE` transaction — no privileged role, no special network condition. I was not able to fully trace `get_secpk1_hint_processor_and_base`'s internal state machine (whether some other code path always pre-populates the segment base before any secp syscall can run), so I cannot rule out an existing guard elsewhere in the request-decoding or hint-processor initialization path that prevents this ordering. This uncertainty should be resolved before treating this as confirmed exploitable.

### Recommendation
Replace the `.expect("Secp segment must be set.")` calls with a proper `Result`-returning error path (e.g., a dedicated `SyscallExecutorBaseError` variant such as "secp operation performed before initialization"), consistent with how `secp256k1_new`/`get_point_from_x` already tolerate a `None` base. Add a regression test that calls `secp256k1_add`/`secp256k1_mul` from a Cairo1 contract without a prior `secp256k1_new` and asserts a graceful `Result::Err` instead of a panic.

### Proof of Concept
Conceptual PoC (pending confirmation of the initialization guard):
1. Deploy a Cairo1 contract whose entry point directly calls `secp256k1_add_syscall(p0, p1)` (or `secp256k1_mul_syscall`) without ever calling `secp256k1_new_syscall` in the same execution context.
2. Submit a normal `INVOKE` transaction targeting this entry point.
3. During execution, `syscall_executor.rs`'s `secp256k1_add`/`secp256k1_mul` calls `get_secpk1_hint_processor_and_base()`; if the segment base is `None`, `.expect("Secp segment must be set.")` panics, crashing the executing process rather than returning a Starknet execution error.

Note: I could not verify within the available investigation whether an earlier code path always guarantees the segment base is set before any secp syscall executes (I was unable to fully read `get_secpk1_hint_processor_and_base`'s implementation in `secp.rs`/`hint_processor.rs`). This should be verified in a follow-up before treating this as a confirmed, exploitable panic.

### Citations

**File:** crates/blockifier/src/execution/syscalls/syscall_executor.rs (L317-328)
```rust
    fn secp256k1_add(
        request: SecpAddRequest,
        vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
        _remaining_gas: &mut u64,
    ) -> Result<SecpAddResponse, Self::Error> {
        let id = syscall_handler.get_secp_id();
        let (secp_processor, optional_secp_segment_base) =
            syscall_handler.get_secpk1_hint_processor_and_base();
        let secp_segment_base = optional_secp_segment_base.expect("Secp segment must be set.");
        Ok(secp_processor.secp_add(request, vm, secp_segment_base, id)?)
    }
```

**File:** crates/blockifier/src/execution/syscalls/syscall_executor.rs (L330-350)
```rust
    fn secp256k1_get_point_from_x(
        request: SecpGetPointFromXRequest,
        vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
        _remaining_gas: &mut u64,
    ) -> Result<SecpGetPointFromXResponse, Self::Error> {
        let id = syscall_handler.get_secp_id();
        let (secp_processor, optional_secp_segment_base) =
            syscall_handler.get_secpk1_hint_processor_and_base();
        Ok(secp_processor.secp_get_point_from_x(vm, request, optional_secp_segment_base, id)?)
    }

    fn secp256k1_get_xy(
        request: SecpGetXyRequest,
        _vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
        _remaining_gas: &mut u64,
    ) -> Result<SecpGetXyResponse, Self::Error> {
        let (secp_processor, _) = syscall_handler.get_secpk1_hint_processor_and_base();
        Ok(secp_processor.secp_get_xy(request)?)
    }
```

**File:** crates/blockifier/src/execution/syscalls/syscall_executor.rs (L352-363)
```rust
    fn secp256k1_mul(
        request: SecpMulRequest,
        vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
        _remaining_gas: &mut u64,
    ) -> Result<SecpMulResponse, Self::Error> {
        let id = syscall_handler.get_secp_id();
        let (secp_processor, optional_secp_segment_base) =
            syscall_handler.get_secpk1_hint_processor_and_base();
        let secp_segment_base = optional_secp_segment_base.expect("Secp segment must be set.");
        Ok(secp_processor.secp_mul(request, vm, secp_segment_base, id)?)
    }
```

**File:** crates/blockifier/src/execution/syscalls/syscall_executor.rs (L377-388)
```rust
    fn secp256r1_add(
        request: SecpAddRequest,
        vm: &mut VirtualMachine,
        syscall_handler: &mut Self,
        _remaining_gas: &mut u64,
    ) -> Result<SecpAddResponse, Self::Error> {
        let id = syscall_handler.get_secp_id();
        let (secp_processor, optional_secp_segment_base) =
            syscall_handler.get_secpr1_hint_processor_and_base();
        let secp_segment_base = optional_secp_segment_base.expect("Secp segment must be set.");
        Ok(secp_processor.secp_add(request, vm, secp_segment_base, id)?)
    }
```
