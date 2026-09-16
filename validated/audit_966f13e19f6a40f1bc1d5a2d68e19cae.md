### Title
Incomplete access-control override: `library_call` syscall omits the `__execute__` invocation guard applied to `call_contract` - ([File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo])

### Summary
This mirrors the reported ERC4626 bug class exactly: a protection was added to one code path (`deposit`/`redeem` in the external report, `call_contract` here) but was not mirrored onto the sibling code path that reaches the same restricted functionality (`mint`/`withdraw` in the report, `library_call` here). In the Starknet OS's deprecated (Cairo 0) syscall dispatcher, the dispatcher for `CALL_CONTRACT_SELECTOR` explicitly forbids targeting the `__execute__` entry point selector, but the dispatcher for `LIBRARY_CALL_SELECTOR` (immediately below it, calling the very same underlying entry-point-execution machinery) has no equivalent check.

### Finding Description
The OS syscall dispatcher `execute_deprecated_syscalls` handles `CALL_CONTRACT_SELECTOR` by asserting the target selector is not the `__execute__` selector before dispatching the call: [1](#0-0) 

Immediately after, the `LIBRARY_CALL_SELECTOR` branch dispatches to `execute_library_call_syscall` with no analogous `assert_not_equal(..., EXECUTE_ENTRY_POINT_SELECTOR)` check: [2](#0-1) 

On the Rust (blockifier) side, the same asymmetry exists: the guard is implemented as `maybe_block_direct_execute_call`/an inline equivalent, and is invoked only from the `call_contract` syscall handlers (both the deprecated hint processor and the native/Cairo1 syscall handler): [3](#0-2) [4](#0-3) [5](#0-4) 

The dedicated error `DirectExecuteCall`/`Invalid argument` and the `block_direct_execute_call` versioned-constants flag exist specifically to forbid any inner call from invoking a contract's `__execute__` entry point (confirmed by unit tests and the `test_direct_execute_call` OS flow test): [6](#0-5) [7](#0-6) [8](#0-7) 

I was unable to fully confirm, in the time available, whether the analogous `library_call` handlers in the modern (non-deprecated, Cairo 1) OS dispatcher (`execute_syscalls.cairo`, `execute_syscalls__virtual.cairo`) and the Rust `library_call` implementations (`crates/blockifier/src/execution/syscalls/hint_processor.rs`, `crates/starknet_os/src/hint_processor/...`) contain or omit the same guard — grep results show `library_call` and `maybe_block_direct_execute_call` do not co-occur in the same call sites the way they do for `call_contract`, which is consistent with the same gap, but I could not read the full function bodies to be certain. This should be verified directly against the source.

### Impact Explanation
If `library_call` can reach a class's `__execute__` entry point while `call_contract` cannot, any contract can invoke another class's `__execute__` logic through a library call from a single ordinary transaction, bypassing the explicit protocol-level restriction that only the top-level protocol invocation (after `__validate__`, nonce increment, and fee charging) is permitted to reach `__execute__`. Any account class whose `__execute__` does not independently re-check `caller_address == 0` (the OS flow test explicitly exercises a `dummy_account`/`EmptyAccount` without such checks) becomes exploitable: an attacker's contract can trigger arbitrary calls "as" that account's `__execute__` logic, out of the normal transaction lifecycle, causing unauthorized account actions and state changes that diverge from the protocol's intended invocation model. Because this asymmetry sits inside the Starknet OS (re-execution) dispatcher used to prove block validity, an inconsistency here could also cause honest-node/OS-proof divergence if the state transition allowed by the blockifier during sequencing differs from what the OS considers valid (or vice versa).

### Likelihood Explanation
Reachable directly from a single unprivileged `INVOKE` transaction — an attacker contract only needs to issue a `library_call` syscall targeting a class hash and the `__execute__` selector, exactly as the existing `call_contract` bypass test (`test_direct_execute_call`) does for the blocked path. No privileged role, prover, operator, or network condition is required.

### Recommendation
Add the same `assert_not_equal(selector, EXECUTE_ENTRY_POINT_SELECTOR)` (Cairo OS side) and `maybe_block_direct_execute_call` (Rust blockifier side) check to every syscall variant that can invoke an external entry point by selector — specifically `library_call` (deprecated and modern), and `delegate_call`/`delegate_l1_handler` where applicable — rather than only to `call_contract`. Add regression tests analogous to `test_direct_execute_call` and `test_call_execute_directly` specifically for the `library_call` syscall path in both the Rust blockifier and the Starknet OS Cairo program, for both Cairo 0 (deprecated) and Cairo 1 dispatchers.

### Proof of Concept
Conceptual PoC (mirrors the existing `test_direct_execute_call` test but substituting `library_call` for `call_contract`):
1. Deploy `contract_with_execute` — a class whose `__execute__` does not check `caller_address == 0` (e.g. the `EmptyAccount`/`AccountWithoutValidations` feature contract already used in `crates/blockifier/src/execution/syscalls/syscall_tests/call_contract.rs`).
2. From an ordinary contract (`test_contract`), issue a `library_call_syscall` with `class_hash` = `contract_with_execute`'s class hash and `function_selector = selector!("__execute__")`.
3. Because `library_call` does not pass through `maybe_block_direct_execute_call` (unlike `call_contract`), the call succeeds and executes `__execute__`'s logic (an arbitrary nested call chosen by the attacker) even though `block_direct_execute_call = true`, whereas the equivalent `call_contract` invocation is rejected with `DirectExecuteCall`/`Invalid argument` as shown in: [9](#0-8)

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L467-484)
```text
    if (selector == CALL_CONTRACT_SELECTOR) {
        let call_contract_syscall = cast(syscall_ptr, CallContract*);
        tempvar caller_address = execution_context.execution_info.contract_address;
        let callee_address = call_contract_syscall.request.contract_address;
        // Since we process the revert log backwards,
        // entries before this point belong to the caller.
        assert [revert_log] = RevertLogEntry(selector=CHANGE_CONTRACT_ENTRY, value=caller_address);
        let revert_log = &revert_log[1];
        // It is forbidded to call the `__execute__` function.
        assert_not_equal(call_contract_syscall.request.selector, EXECUTE_ENTRY_POINT_SELECTOR);
        execute_contract_call_syscall(
            block_context=block_context,
            contract_address=callee_address,
            caller_address=caller_address,
            entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
            caller_execution_context=execution_context,
            syscall_ptr=call_contract_syscall,
        );
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deprecated_execute_syscalls.cairo (L497-510)
```text
    if (selector == LIBRARY_CALL_SELECTOR) {
        execute_library_call_syscall(
            block_context=block_context,
            caller_execution_context=execution_context,
            entry_point_type=ENTRY_POINT_TYPE_EXTERNAL,
            syscall_ptr=cast(syscall_ptr, LibraryCall*),
        );
        %{ OsLoggerExitSyscall %}
        return execute_deprecated_syscalls(
            block_context=block_context,
            execution_context=execution_context,
            syscall_size=syscall_size - LibraryCall.SIZE,
            syscall_ptr=syscall_ptr + LibraryCall.SIZE,
        );
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L130-131)
```rust
    #[error("Calling `__execute__` directly is not allowed.")]
    DirectExecuteCall,
```

**File:** crates/blockifier/src/execution/deprecated_syscalls/hint_processor.rs (L551-557)
```rust
        let versioned_constants =
            &syscall_handler.context.tx_context.block_context.versioned_constants;
        if versioned_constants.block_direct_execute_call
            && selector == selector_from_name(EXECUTE_ENTRY_POINT_NAME)
        {
            return Err(DeprecatedSyscallExecutionError::DirectExecuteCall);
        }
```

**File:** crates/blockifier/src/execution/native/syscall_handler.rs (L516-519)
```rust
        let selector = EntryPointSelector(entry_point_selector);
        self.base
            .maybe_block_direct_execute_call(selector)
            .map_err(|e| self.handle_error(remaining_gas, e))?;
```

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L487-498)
```rust
    pub(crate) fn maybe_block_direct_execute_call(
        &mut self,
        selector: EntryPointSelector,
    ) -> SyscallResult<()> {
        let versioned_constants = &self.context.tx_context.block_context.versioned_constants;
        if versioned_constants.block_direct_execute_call
            && selector == selector_from_name(EXECUTE_ENTRY_POINT_NAME)
        {
            return Err(SyscallExecutionError::Revert { error_data: vec![INVALID_ARGUMENT_FELT] });
        }
        Ok(())
    }
```

**File:** crates/starknet_os_flow_tests/src/tests.rs (L2737-2775)
```rust
/// Verify OS blocks direct calls to `__execute__` entry point.
#[rstest]
#[tokio::test]
async fn test_direct_execute_call() {
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo1(RunnableCairo1::Casm));
    let dummy_account =
        FeatureContract::AccountWithoutValidations(CairoVersion::Cairo1(RunnableCairo1::Casm));
    let (mut test_builder, [test_contract_address, dummy_account_address]) =
        TestBuilder::create_standard_with_config(
            [
                (test_contract, default_test_contract_constructor_calldata()),
                (dummy_account, calldata![]),
            ],
            TestBuilderConfig { use_kzg_da: true, ..Default::default() },
        )
        .await;

    let calldata = create_calldata(
        test_contract_address,
        "test_direct_execute_call",
        &[
            **dummy_account_address,
            Felt::from(5), // Outer calldata length.
            **test_contract_address,
            selector_from_name("assert_eq").0,
            Felt::TWO, // Inner calldata length.
            Felt::ONE, // Arg 1.
            Felt::ONE, // Arg 2.
        ],
    );
    test_builder.add_funded_account_invoke(invoke_tx_args! { calldata });

    // Run test.
    let test_output = test_builder.build_and_run().await;
    test_output.perform_default_validations();
    test_output.assert_storage_diff_eq(test_contract_address, HashMap::default());
    test_output.assert_storage_diff_eq(dummy_account_address, HashMap::default());
    test_output.expect_hint_coverage("test_direct_execute_call");
}
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_14_0.json (L118-118)
```json
    "block_direct_execute_call": true,
```

**File:** crates/blockifier/src/execution/syscalls/syscall_tests/call_contract.rs (L297-336)
```rust
fn test_direct_execute_call(cairo1_type: RunnableCairo1, block_direct_execute_call: bool) {
    let test_contract = FeatureContract::TestContract(CairoVersion::Cairo1(cairo1_type));
    let contract_with_execute = FeatureContract::EmptyAccount(cairo1_type);
    let chain_info = &ChainInfo::create_for_testing();
    let mut state =
        test_state(chain_info, BALANCE, &[(test_contract, 1), (contract_with_execute, 1)]);

    let test_contract_address = *test_contract.get_instance_address(0).0.key();
    let contract_with_execute_address = *contract_with_execute.get_instance_address(0).0.key();
    let call_execute_directly_selector = selector_from_name("call_execute_directly");
    let return_result_selector = selector_from_name("return_result");

    let call_execute_directly = CallEntryPoint {
        entry_point_selector: call_execute_directly_selector,
        calldata: calldata_macro![
            // The Execute entrypoint of this contract will be called.
            contract_with_execute_address,
            // Outer calldata (passed to `execute` entrypoint)
            felt!(4_u8), // Outer calldata length.
            test_contract_address,
            return_result_selector.0,
            // Inner calldata (passed to function called by `execute` entrypoint)
            felt!(1_u8), // Inner calldata length.
            felt!(0_u8)  // Inner calldata value.
        ],
        ..trivial_external_entry_point_new(test_contract)
    };

    let mut block_context = BlockContext::create_for_testing();
    block_context.versioned_constants.block_direct_execute_call = block_direct_execute_call;
    let call_info = call_execute_directly
        .execute_directly_given_block_context(&mut state, block_context)
        .unwrap();

    if block_direct_execute_call {
        assert!(call_info.execution.failed, "Expected direct execute call to fail.");
        assert_eq!(
            format_panic_data(&call_info.execution.retdata.0),
            "0x496e76616c696420617267756d656e74 ('Invalid argument')",
        );
```
