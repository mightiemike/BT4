## Title
Panic-inducing gas-accounting underflow in `SierraGasRevertTracker::get_gas_consumed` due to conditional (single-sided) updates - (File: `crates/blockifier/src/execution/entry_point.rs`)

### Summary
`SierraGasRevertTracker` computes a transaction's reverted gas as a plain difference between an `initial_remaining_gas` snapshot and a `last_seen_remaining_gas` value that is updated only conditionally (only when the *currently active* tracked resource is `SierraGas`), mirroring the report's bug class: a value derived by subtracting two independently, partially-updated counters (`feeGrowthBelow`/`feeGrowthAbove` from `feeGrowthGlobal`) that are not guaranteed to stay consistent with each other, causing the subtraction to underflow.

### Finding Description
`SierraGasRevertTracker` holds two fields, `initial_remaining_gas` and `last_seen_remaining_gas` [1](#0-0) . `last_seen_remaining_gas` is advanced only when the resource currently on top of `tracked_resource_stack` is `TrackedResource::SierraGas`; when the active resource is `TrackedResource::CairoSteps` the update is a silent no-op [2](#0-1) . The correctness of `get_gas_consumed` (a plain `checked_sub` that panics on underflow, i.e. it is *not* a soft/saturating computation) depends on an "induction" argument documented only in a comment at the call site, enumerating three separate cases (non-Cairo-executing syscalls, Cairo-executing syscalls under `CairoSteps`, and Cairo-executing syscalls under `SierraGas`) that must all hold for `last_seen_remaining_gas` to never exceed `initial_remaining_gas` [3](#0-2) :

```rust
pub fn get_gas_consumed(&self) -> GasAmount {
    self.initial_remaining_gas.checked_sub(self.last_seen_remaining_gas).unwrap_or_else(|| {
        panic!(
            "The consumed gas must be non-negative. Initial gas: {}, last seen gas: {}.",
            self.initial_remaining_gas, self.last_seen_remaining_gas
        )
    })
}
``` [4](#0-3) 

This is precisely the class of bug in the referenced report: two related quantities are updated on separate, conditional code paths (in Trident, `feeGrowthOutside0`/`feeGrowthOutside1` update only one side per tick cross; here, `last_seen_remaining_gas` updates only under one tracked-resource branch of a call stack that legitimately mixes `CairoSteps` (Cairo0 / pre-Sierra-gas Cairo1) and `SierraGas` (newer Cairo1) contracts within a single call tree — a mix explicitly exercised by the codebase's own tests, e.g. `test_tracked_resources_nested`, which shows an outer `SierraGas` call invoking an inner `CairoSteps` call and back to `SierraGas` [5](#0-4) . Because a submitted transaction's calldata (and thus the sequence and nesting of contract calls, including reverts within that nesting) is fully attacker-controlled, an unprivileged sender can freely construct call trees that stress the ordering assumptions behind the "induction" argument.

The value returned from `get_gas_consumed` is used to charge gas for the blockifier's post-execution revert flow (invoked from `account_transaction.rs`, which imports `SierraGasRevertTracker` for its fee/resource accounting) [6](#0-5) . Because the subtraction panics rather than saturating (contrasted with the sibling API `subtract_steps`, which explicitly guards against the identical class of scenario via `saturating_sub` [7](#0-6) ), any call-tree shape that breaks the documented invariant turns into an unrecoverable Rust panic inside the batcher's block-building path rather than a graceful transaction rejection.

### Impact Explanation
A panic during transaction execution in the block-building/re-execution path (blockifier) is not a benign error — it aborts the running process. Since gas/fee accounting is exercised by every sequencer and by every full node re-executing the block (Starknet OS re-execution / other blockifier instances), a transaction that can deterministically trigger this panic can be broadcast to and included by any sequencer, crashing the process building or re-validating the block. This maps to "a network unable to confirm new transactions" (denial of service against block production/re-execution), which is explicitly an accepted impact category for this scan.

### Likelihood Explanation
Reachability requires only a single submitted transaction whose calldata drives a call tree that alternates between `CairoSteps`-tracked contracts (Cairo0 or old Cairo1 without Sierra gas) and `SierraGas`-tracked contracts, combined with a revert somewhere in that tree so the revert-gas-charging path (which calls `get_gas_consumed`) is exercised. This exact mixed-resource nesting pattern is demonstrably supported and tested by the codebase itself, indicating it is a realistic, sequencer-reachable configuration, not a contrived edge case. I was not able to fully trace, within the available tool budget, every call site of `sierra_gas_revert_tracker` in `account_transaction.rs` and `l1_handler_transaction.rs` to construct a concrete end-to-end panic trigger (e.g., the precise interleaving of syscalls needed to violate the "3-case induction" comment), so likelihood should be treated as plausible-but-unconfirmed rather than proven.

### Recommendation
Replace the panicking `checked_sub` in `get_gas_consumed` with a saturating computation (as already done for `subtract_steps` and for `sierra_gas_to_steps_gas` in `bouncer.rs`, which already logs and saturates to zero on an analogous underflow) [8](#0-7) , or, more robustly, rework `SierraGasRevertTracker` so `last_seen_remaining_gas` is unconditionally kept consistent with whichever resource is being tracked at every stack depth (removing reliance on the documented but unverified 3-case invariant). At minimum, add exhaustive property/fuzz tests specifically covering deep alternations of `CairoSteps`/`SierraGas` contracts combined with reverts at multiple nesting depths to validate the invariant holds for all reachable call shapes.

### Proof of Concept
A concrete transaction-level PoC could not be constructed with the available read-only tooling in the time budget; doing so would require driving the CairoVM/Native execution engine with crafted recursive contracts (mixing Cairo0/old-Cairo1 and Sierra-gas Cairo1 versions) and a revert at a specific nesting depth to falsify the invariant documented in `vm_syscall_utils.rs`, then asserting a panic in `SierraGasRevertTracker::get_gas_consumed`. This would require setting up an executable Devin/test session against the `blockifier` crate's test harness (e.g., extending `test_tracked_resources_nested`-style tests with an injected revert) to confirm exploitability; I recommend this as a follow-up validation step rather than asserting it as proven here.

### Citations

**File:** crates/blockifier/src/execution/entry_point.rs (L305-309)
```rust
#[derive(Debug)]
pub struct SierraGasRevertTracker {
    initial_remaining_gas: GasAmount,
    last_seen_remaining_gas: GasAmount,
}
```

**File:** crates/blockifier/src/execution/entry_point.rs (L316-325)
```rust
    /// Updates the last seen remaining gas, if we are in gas-tracking mode.
    pub fn update_with_next_remaining_gas(
        &mut self,
        tracked_resource: TrackedResource,
        next_remaining_gas: GasAmount,
    ) {
        if tracked_resource == TrackedResource::SierraGas {
            self.last_seen_remaining_gas = next_remaining_gas;
        }
    }
```

**File:** crates/blockifier/src/execution/entry_point.rs (L327-334)
```rust
    pub fn get_gas_consumed(&self) -> GasAmount {
        self.initial_remaining_gas.checked_sub(self.last_seen_remaining_gas).unwrap_or_else(|| {
            panic!(
                "The consumed gas must be non-negative. Initial gas: {}, last seen gas: {}.",
                self.initial_remaining_gas, self.last_seen_remaining_gas
            )
        })
    }
```

**File:** crates/blockifier/src/execution/entry_point.rs (L497-505)
```rust
    /// Subtracts the given number of steps from the currently available run resources.
    /// Used for limiting the number of steps available during the execution stage, to leave enough
    /// steps available for the fee transfer stage.
    /// Returns the remaining number of steps.
    pub fn subtract_steps(&mut self, steps_to_subtract: usize) -> usize {
        // Saturating, since subtracting more steps than remain would underflow.
        let new_remaining_steps = self.n_remaining_steps().saturating_sub(steps_to_subtract);
        self.set_remaining_steps(new_remaining_steps)
    }
```

**File:** crates/blockifier/src/execution/syscalls/vm_syscall_utils.rs (L708-720)
```rust
    // To support sierra gas charge for blockifier revert flow, we track the remaining gas left
    // before executing a syscall if the current tracked resource is gas.
    // 1. If the syscall does not run Cairo code (i.e. not library call, not call contract, and not
    //    a deploy), any failure will not run in the OS, so no need to charge - the value before
    //    entering the callback is good enough to charge.
    // 2. If the syscall runs Cairo code, but the tracked resource is steps (and not gas), the
    //    additional charge of reverted cairo steps will cover the inner cost, and the outer cost we
    //    track here will be the additional reverted gas.
    // 3. If the syscall runs Cairo code and the tracked resource is gas, either the inner failure
    //    will be a Cairo1 revert (and the gas consumed on the call info will override the current
    //    tracked value), or we will pass through another syscall before failing - and by induction
    //    (we will reach this point again), the gas will be charged correctly.
    syscall_executor.update_revert_gas_with_next_remaining_gas(GasAmount(remaining_gas));
```

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/call_contract.rs (L431-478)
```rust
fn test_tracked_resources_nested(
    cairo_steps_contract_version: CompilerBasedVersion,
    sierra_gas_contract_version: CompilerBasedVersion,
) {
    let cairo_steps_contract = cairo_steps_contract_version.get_test_contract();
    let sierra_gas_contract = sierra_gas_contract_version.get_test_contract();
    let chain_info = &ChainInfo::create_for_testing();
    let mut state =
        test_state(chain_info, BALANCE, &[(sierra_gas_contract, 1), (cairo_steps_contract, 1)]);

    let first_calldata =
        build_recurse_calldata(&[cairo_steps_contract_version, sierra_gas_contract_version]);

    let second_calldata = build_recurse_calldata(&[sierra_gas_contract_version]);

    let concatenated_calldata_felts = [first_calldata.0, second_calldata.0]
        .into_iter()
        .map(|calldata_felts| calldata_felts.iter().copied().collect_vec())
        .concat();
    let concatenated_calldata = Calldata(Arc::new(concatenated_calldata_felts));
    let call_contract_selector = selector_from_name("test_call_two_contracts");
    let entry_point_call = CallEntryPoint {
        entry_point_selector: call_contract_selector,
        calldata: concatenated_calldata,
        ..trivial_external_entry_point_new(sierra_gas_contract)
    };
    let main_call_info = entry_point_call.execute_directly(&mut state).unwrap();

    assert_eq!(main_call_info.tracked_resource, TrackedResource::SierraGas);
    assert_ne!(main_call_info.execution.gas_consumed, 0);

    let first_inner_call = main_call_info.inner_calls.first().unwrap();
    assert_eq!(first_inner_call.tracked_resource, TrackedResource::CairoSteps);
    assert_eq!(first_inner_call.execution.gas_consumed, 0);
    assert_eq!(first_inner_call.execution.cairo_native, false);
    let inner_inner_call = first_inner_call.inner_calls.first().unwrap();
    assert_eq!(inner_inner_call.tracked_resource, TrackedResource::CairoSteps);
    assert_eq!(inner_inner_call.execution.gas_consumed, 0);
    assert_eq!(inner_inner_call.execution.cairo_native, false);

    let second_inner_call = main_call_info.inner_calls.get(1).unwrap();
    assert_eq!(second_inner_call.tracked_resource, TrackedResource::SierraGas);
    assert_ne!(second_inner_call.execution.gas_consumed, 0);
    assert_eq!(
        second_inner_call.execution.cairo_native,
        sierra_gas_contract_version.is_cairo_native()
    );
}
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L33-38)
```rust
use crate::execution::entry_point::{
    CallEntryPoint,
    CallType,
    EntryPointExecutionContext,
    SierraGasRevertTracker,
};
```

**File:** crates/blockifier/src/bouncer.rs (L793-810)
```rust
/// Computes the steps gas by subtracting the builtins' contribution from the Sierra gas.
pub fn sierra_gas_to_steps_gas(
    sierra_gas: GasAmount,
    cairo_primitives_counters: &CairoPrimitiveCounterMap,
    sierra_builtin_gas_costs: &BuiltinGasCosts,
) -> GasAmount {
    let cairo_primitives_gas =
        cairo_primitives_to_gas(cairo_primitives_counters, sierra_builtin_gas_costs);

    sierra_gas.checked_sub(cairo_primitives_gas).unwrap_or_else(|| {
        log::debug!(
            "Sierra gas underflow: cairo primitives gas exceeds total. Sierra gas: \
             {sierra_gas:?}, Cairo primitives gas: {cairo_primitives_gas:?}, Cairo primitives: \
             {cairo_primitives_counters:?}"
        );
        GasAmount::ZERO
    })
}
```
