Confirmed: the Starknet OS Cairo implementation of `execute_replace_class` in `syscall_impls.cairo` lacks the "class is declared" check that exists in the Rust blockifier (`syscall_base.rs::replace_class`) and in the deprecated syscall handler (`hint_processor.rs::replace_class`). This is a genuine reachable divergence.

### Title
OS `execute_replace_class` omits declared-class check present in Blockifier, causing execution divergence and re-execution/proof mismatch - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo])

### Summary
The Starknet OS's Cairo implementation of the `replace_class` syscall (`execute_replace_class` in `syscall_impls.cairo`) unconditionally accepts any `class_hash` supplied by the calling contract and writes it into `contract_state_changes`, without verifying that the class hash corresponds to a declared (and Cairo1) contract class. This mirrors the referral-code bug class: a privileged/authorized write path (here, the syscall execution trusted by the OS) mutates a critical mapping (`contract_address -> class_hash`) without validating a precondition (that the target value is a legitimately declared class) that the parallel/authoritative implementation (Blockifier) does enforce.

### Finding Description
In the Rust Blockifier, `replace_class` in `crates/blockifier/src/execution/syscalls/syscall_base.rs` first calls `self.state.get_compiled_class(class_hash)?` to ensure the class is declared, and rejects the call with `ForbiddenClassReplacement` if it is not a Cairo1 class: [1](#0-0) 
Similarly, the deprecated (Cairo0) syscall path in `hint_processor.rs` enforces the class must be declared before the class hash is set: [2](#0-1) 

However, the corresponding Cairo implementation used by the Starknet OS (which re-executes transactions to prove state transitions) explicitly documents that this validation is missing: [3](#0-2) 
The `// TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.` comment confirms the OS blindly performs `dict_update` on `contract_state_changes`, setting the contract's class hash to whatever value was supplied by the calling contract, with no verification against the declared-classes/compiled-class mapping.

An unprivileged contract (reachable by any account contract executing a `replace_class` syscall via a regular Invoke transaction) can supply an arbitrary, undeclared `class_hash`. Because Blockifier (the component that actually builds the block and computes the committed state diff) rejects such a call, the transaction as included in the block will either revert or otherwise not apply the class-hash change. But the Starknet OS, which independently re-executes the same transaction against the same block inputs to generate the STARK proof of the state transition, will apply the change unconditionally since it lacks the check.

### Impact Explanation
This produces a divergence between what the sequencer's Blockifier computed as the canonical state diff/committed root for the block and what the Starknet OS computes when re-executing that same block to generate a proof. Concretely:
- If the OS accepts a class-hash change that Blockifier rejected, the OS-computed state (and consequently the Patricia tree / state commitment it produces) will not match the state actually committed by the sequencer for that block.
- This is a form of "wrong committed root" / honest-node divergence: proof generation for an otherwise valid block would either fail (network unable to confirm/finalize new blocks, since proofs cannot be produced that match the committed state) or, in the worst case, allow a state transition to be proven that does not match Blockifier's actual execution semantics, letting a contract set its own class hash to a completely arbitrary, non-existent/undeclared value in the OS-provable execution path.
- Setting a contract's class hash to an undeclared class also breaks subsequent invariants that other OS logic (and the committer) assumes about `declared_contracts`/class hash validity, which can cascade into further consensus-relevant miscomputation.

### Likelihood Explanation
Reachable directly from an unprivileged transaction sender: any account contract can invoke `replace_class_syscall` (as already demonstrated by the test contract's `test_replace_class` function) with an arbitrary `class_hash` argument, requiring no special privileges beyond deploying/using a standard account contract: [4](#0-3) 
The missing check is explicitly acknowledged by a TODO comment in production code, confirming it is a known-but-unfixed gap rather than a hypothetical.

### Recommendation
Add the missing precondition check in `execute_replace_class` (`syscall_impls.cairo`) mirroring the Rust Blockifier logic: verify (via `dict_read`/hint on `contract_class_changes` or the declared-classes mapping) that `class_hash` corresponds to a declared class before performing the `dict_update` on `contract_state_changes`, and reject/revert (or emit the equivalent failure response) otherwise, matching the `ForbiddenClassReplacement`/`is not declared` semantics enforced in `crates/blockifier/src/execution/syscalls/syscall_base.rs`.

### Proof of Concept
1. Deploy an account contract that, during `__execute__`, calls `replace_class_syscall(class_hash=X)` where `X` is not a declared class hash (as in `test_replace_class` at [4](#0-3) ).
2. Submit this Invoke transaction. Blockifier's `replace_class` (`crates/blockifier/src/execution/syscalls/syscall_base.rs:369-378`) will error with `StateError::UndeclaredClassHash`/`ForbiddenClassReplacement`, causing the inner call to revert; the block's committed state diff for this contract's class hash is unchanged.
3. When the Starknet OS re-executes the same block/transaction using `execute_replace_class` (`syscall_impls.cairo:881-920`), it performs `dict_update` unconditionally, recording a class-hash change to `X` for the contract in `contract_state_changes` — producing a different final state than the one actually committed by Blockifier, resulting in a state/commitment mismatch for that block.

### Citations

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L369-378)
```rust
    pub fn replace_class(&mut self, class_hash: ClassHash) -> SyscallResult<()> {
        // Ensure the class is declared (by reading it), and of type V1.
        let compiled_class = self.state.get_compiled_class(class_hash)?;

        if !is_cairo1(&compiled_class) {
            return Err(SyscallExecutionError::ForbiddenClassReplacement { class_hash });
        }
        self.state.set_class_hash_at(self.call.storage_address, class_hash)?;
        Ok(())
    }
```

**File:** crates/blockifier/src/execution/syscalls/hint_processor.rs (L795-807)
```rust
            Hint::Starknet(hint) => Ok(execute_next_syscall(self, vm, hint)?),
            Hint::External(_) => {
                panic!("starknet should never accept classes with external hints!")
            }
        }
    }

    /// Trait function to store hint in the hint processor by string.
    fn compile_hint(
        &self,
        hint_code: &str,
        _ap_tracking_data: &ApTracking,
        _reference_ids: &HashMap<String, usize>,
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L900-920)
```text
    let class_hash = request.class_hash;

    // TODO(Yoni, 1/1/2026): Check that there is a declared contract class with the given hash.
    local state_entry: StateEntry*;
    %{ GetContractAddressStateEntry %}

    tempvar new_state_entry = new StateEntry(
        class_hash=class_hash, storage_ptr=state_entry.storage_ptr, nonce=state_entry.nonce
    );

    dict_update{dict_ptr=contract_state_changes}(
        key=contract_address,
        prev_value=cast(state_entry, felt),
        new_value=cast(new_state_entry, felt),
    );

    assert [revert_log] = RevertLogEntry(selector=CHANGE_CLASS_ENTRY, value=state_entry.class_hash);
    let revert_log = &revert_log[1];

    return ();
}
```

**File:** crates/blockifier_test_utils/resources/feature_contracts/cairo1/cairo_steps_test_contract.cairo (L200-203)
```text
    #[external(v0)]
    fn test_replace_class(self: @ContractState, class_hash: ClassHash) {
        syscalls::replace_class_syscall(class_hash).unwrap_syscall();
    }
```
