This confirms the analog. In `charge_fee` (Starknet OS Cairo), the ERC20 `transfer` entry point is invoked through `non_reverting_select_execute_entry_point_func`, which only asserts `is_reverted = 0` — i.e., that the call itself didn't revert — but never reads or checks the `retdata` (the `(success: felt)` boolean the ERC20 `transfer` function returns). This is the direct Cairo/StarknetOS analog of the Solidity `cvg.transfer(...)` call whose boolean return value is ignored.

### Title
Unchecked ERC20 `transfer` return value in `charge_fee` allows fee bypass without reverting - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo)

### Summary
The Starknet OS `charge_fee` function executes the fee-token `transfer` entry point via `non_reverting_select_execute_entry_point_func`, but only checks that the call did not revert. It never inspects the `retdata` returned by the ERC20 `transfer` function, which per the standard fee-token ABI returns a boolean `(success: felt)`. If the fee token contract's `transfer` implementation returns `success = 0` (false) without reverting — a legal ERC20 behavior — the OS treats the fee charge as having succeeded.

### Finding Description
`charge_fee` builds an `ExecutionContext` targeting the fee token's `TRANSFER_ENTRY_POINT_SELECTOR` and invokes it via `non_reverting_select_execute_entry_point_func`: [1](#0-0) 

That helper only asserts the call was not reverted, and discards `retdata` (the success boolean) entirely for the fee-transfer caller: [2](#0-1) 

Compare this with `run_validate`, which explicitly does read and assert on the `retdata` returned from `__validate__`: [3](#0-2) 

No equivalent retdata check exists for the fee `transfer` call — the ERC20 `transfer` function's documented return value `(success: felt)` (visible in the fee-token class metadata) is never inspected: [4](#0-3) 

This is the exact bug class from the external report: `cvg.transfer(...)` without checking the boolean return value. Here the sequencer's own OS re-execution path exhibits the identical omission when charging transaction fees to the sequencer.

### Impact Explanation
If the well-known fee-token contract's `transfer` implementation ever returns `false` on failure (instead of reverting) under any edge case (e.g., a future STRK/ETH fee-token upgrade, or a governance-controlled implementation change reachable at the class-hash configured in `starknet_os_config.fee_token_address`), the OS would proceed as if the fee had been paid, while the sender's balance is not actually decremented and the sequencer's balance is not credited. This causes the sequencer's canonical state to record a fee credit to itself that was never backed by an actual balance movement, corrupting the committed state root/fee accounting versus what genuinely happened in the fee token's storage, and let the transaction execute for free. This is a permanent-loss/incorrect-state-commitment class issue for the network's fee accounting since blockifier's Rust-side `execute_fee_transfer` (`crates/blockifier/src/transaction/account_transaction.rs:550-591`) similarly only propagates `Err` on Cairo-level reverts via `?`, and does not separately assert on the ERC20 success boolean, so the same blind spot exists on the block-building (blockifier) side that produces the state diff the OS is meant to re-verify.

### Likelihood Explanation
Likelihood is constrained by the fact that the current fee token implementation is expected to revert on insufficient balance rather than return `false`. However, because the sequencer's protocol-level correctness must not depend on assumptions about the specific fee-token contract's internal behavior — the OS explicitly reads `fee_token_address` from `starknet_os_config` and dispatches by class hash without hardcoding/validating the specific bytecode — any deviation in that contract's `transfer` semantics (a false return instead of revert) is unauthenticated by the OS and is reachable simply by anyone submitting a normal transaction once the fee-token class exhibits that behavior. The blockifier side has identical missing validation.

### Recommendation
In `charge_fee` (Cairo OS), read the `retdata` returned by `non_reverting_select_execute_entry_point_func` (mirroring the pattern already used in `run_validate`) and assert that it decodes to `success = 1`/`TRUE`. Symmetrically, in blockifier's `execute_fee_transfer` (`crates/blockifier/src/transaction/account_transaction.rs`), inspect the `CallInfo.execution.retdata` of the fee-transfer call and fail the transaction if the returned boolean is not true, rather than only checking for a Cairo-level revert.

### Proof of Concept
Not applicable as a runnable exploit — the codebase's index does not include the concrete deployed fee-token Cairo/Sierra source guaranteed to be used in production, so proving today's ERC20 fee-token contract returns `false` rather than reverting could not be confirmed via available tools. The vulnerability is a structural code-review finding: `non_reverting_select_execute_entry_point_func` (`execute_transaction_utils.cairo:179-197`) discards `retdata`, and `charge_fee` (`transaction_impls.cairo:160-164`) never checks it, so any fee-token implementation whose `transfer` returns `success = 0` without reverting would be silently accepted as a successful fee charge.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L160-164)
```text
    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
    return ();
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L148-156)
```text
    // The __validate__ function should not revert.
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
    if (is_deprecated == 0) {
        %{ CheckRetdataForDebug %}
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L179-197)
```text
// Same as `select_execute_entry_point_func`, but does not support reverts and does
// not have an implicit 'revert_log' argument.
func non_reverting_select_execute_entry_point_func{
    range_check_ptr,
    remaining_gas: felt,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, execution_context: ExecutionContext*) -> (
    retdata_size: felt, retdata: felt*, is_deprecated: felt
) {
    let revert_log = init_revert_log();
    let (is_reverted, retdata_size, retdata, is_deprecated) = select_execute_entry_point_func{
        revert_log=revert_log
    }(block_context=block_context, execution_context=execution_context);
    assert is_reverted = 0;
    return (retdata_size, retdata, is_deprecated);
}
```

**File:** crates/apollo_rpc/resources/erc20_fee_contract_class.json (L4459-4473)
```json
            "__wrappers__.transfer_encode_return.Args": {
                "full_name": "__wrappers__.transfer_encode_return.Args",
                "members": {
                    "range_check_ptr": {
                        "cairo_type": "felt",
                        "offset": 1
                    },
                    "ret_value": {
                        "cairo_type": "(success: felt)",
                        "offset": 0
                    }
                },
                "size": 2,
                "type": "struct"
            },
```
