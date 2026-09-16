### Title
Unchecked ERC20 `transfer` success return value in Starknet OS fee charging - (File: crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo)

### Summary
The Starknet OS `charge_fee` function executes the fee-token `transfer` entry point on behalf of the transaction sender but never inspects the `(success: felt)` return value of that call, unlike the analogous `run_validate` flow which explicitly checks the callee's return data.

### Finding Description
`charge_fee` builds a `TransferCallData` and invokes the fee token contract's `transfer` entry point via `non_reverting_select_execute_entry_point_func`, then simply returns without examining `retdata`: [1](#0-0) 

Compare this with `run_validate`, which calls the exact same helper but explicitly asserts the returned data is the expected `VALIDATED` sentinel before proceeding: [2](#0-1) 

`non_reverting_select_execute_entry_point_func` itself only asserts that the call did not revert (`is_reverted = 0`); it returns `retdata_size`/`retdata` to the caller for validation, but `charge_fee` discards these values entirely: [3](#0-2) 

The ERC20 `transfer`/`transferFrom` ABI is defined to return `(success: felt)` — a boolean-like felt that a conforming implementation could return as `FALSE` without reverting the call: [4](#0-3) 

The parallel Rust-side blockifier implementation (`execute_fee_transfer` in `account_transaction.rs`) has the same gap: it executes the `transfer` call and directly wraps the resulting `CallInfo` as the transaction's `fee_transfer_call_info` without checking `retdata` for a `TRUE`/success value: [5](#0-4) 

The Starknet OS is the sequencer's re-execution/state-commitment path (used to build/verify the STARK proof and to independently re-derive block state), so this logic is directly on the block-building/state-commitment path reachable by any submitted transaction that pays fees.

### Impact Explanation
If the fee token contract's `transfer` entry point returns `success = FALSE` (a felt value of `0`) instead of reverting — which is valid per the ERC20-on-Starknet interface contract — both the blockifier fee-charging path and the Starknet OS `charge_fee` path treat the transaction as having successfully paid its fee. The account's balance decrement performed by the fee-token contract's internal logic is what actually matters for balance correctness (the `ERC20_transfer` internal call still executes and moves balances in the reference implementation), so for the canonical fee token this specific case is largely theoretical; however, this analysis is limited to the canonical fee-token implementation shipped in this repo. Any deployment where the configured fee-token class returns `FALSE` without reverting (a valid ERC20 pattern) would let the sequencer report a successful transaction and charge the sequencer's own balance increase in the OS/blockifier bookkeeping while the sender's balance was never actually decremented by the same amount, if the underlying implementation's `transfer` logic and its return-value semantics diverge (e.g., a mock/custom fee-token where the "success" felt is decoupled from the internal balance update on some code path). This would result in an incorrect committed state (wrong balances → wrong state root / block hash) or improper fee accounting, without the sequencer detecting it, because the return value is silently ignored on both the OS and blockifier sides.

### Likelihood Explanation
Likelihood is constrained by the fact that the canonical fee-token contract used by this codebase's tests always ties the `success` return value to an actual internal transfer, and any genuine revert scenario (insufficient balance/allowance, etc.) is already caught via the "must not revert" assertion (`assert is_reverted = 0`) in both `non_reverting_select_execute_entry_point_func` and the Rust `.execute()?` propagation. The vulnerability is only reachable if the network's actual fee-token class implementation is (or becomes) one that can return `FALSE` from `transfer` without reverting and without fully updating balances — a configuration/implementation-level precondition external to the sequencer code itself, not something a plain unprivileged transaction sender can force with the current, in-repo fee-token implementation.

### Recommendation
In `charge_fee` (`transaction_impls.cairo`), inspect `retdata_size`/`retdata` returned by `non_reverting_select_execute_entry_point_func` and assert the fee-token call returned `TRUE`/success, mirroring the pattern already used for `run_validate`'s `VALIDATED` check. Symmetrically, in `execute_fee_transfer` (`account_transaction.rs`), after calling `fee_transfer_call.execute(...)`, verify `call_info.execution.retdata` equals the expected success felt before treating the fee transfer as valid, and propagate a `TransactionFeeError` if it does not.

### Proof of Concept
Not directly demonstrable purely from this repo, since the shipped fee-token contract's `transfer`/`ERC20_transfer` always reverts on insufficient balance/allowance rather than returning `FALSE` on failure: [6](#0-5) 
Concretely triggering the divergence requires a fee-token class (declared/configured for a chain) whose `transfer` implementation returns `FALSE` on some failure path without reverting — a scenario the code as written cannot rule out because `charge_fee` (OS) and `execute_fee_transfer` (blockifier) never check the returned success value, unlike `run_validate`'s explicit `assert retdata[0] = VALIDATED` check shown above.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/transaction_impls.cairo (L160-165)
```text
    let remaining_gas = DEFAULT_INITIAL_GAS_COST;
    non_reverting_select_execute_entry_point_func{remaining_gas=remaining_gas}(
        block_context=block_context, execution_context=&execution_context
    );
    return ();
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/execute_transaction_utils.cairo (L149-158)
```text
    let (retdata_size, retdata, is_deprecated) = non_reverting_select_execute_entry_point_func(
        block_context=block_context, execution_context=validate_execution_context
    );
    if (is_deprecated == 0) {
        %{ CheckRetdataForDebug %}
        assert retdata_size = 1;
        assert retdata[0] = VALIDATED;
    }

    return ();
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

**File:** crates/apollo_rpc_execution/resources/erc20_fee_contract_class.json (L211-226)
```json
                    "type": "felt"
                },
                {
                    "name": "amount",
                    "type": "Uint256"
                }
            ],
            "name": "transfer",
            "outputs": [
                {
                    "name": "success",
                    "type": "felt"
                }
            ],
            "type": "function"
        },
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L550-591)
```rust
    fn execute_fee_transfer(
        state: &mut dyn State,
        tx_context: Arc<TransactionContext>,
        actual_fee: Fee,
    ) -> TransactionExecutionResult<CallInfo> {
        // The least significant 128 bits of the amount transferred.
        let lsb_amount = Felt::from(actual_fee.0);
        // The most significant 128 bits of the amount transferred.
        let msb_amount = Felt::ZERO;

        let TransactionContext { block_context, tx_info } = tx_context.as_ref();
        let storage_address = tx_context.fee_token_address();
        // The fee contains the cost of running this transfer, and the token contract is
        // well known to the sequencer, so there is no need to limit its run.
        let mut remaining_gas_for_fee_transfer =
            block_context.versioned_constants.os_constants.gas_costs.base.default_initial_gas_cost;
        let fee_transfer_call = CallEntryPoint {
            class_hash: None,
            code_address: None,
            entry_point_type: EntryPointType::External,
            entry_point_selector: selector_from_name(constants::TRANSFER_ENTRY_POINT_NAME),
            calldata: calldata![
                *block_context.block_info.sequencer_address.0.key(), // Recipient.
                lsb_amount,
                msb_amount
            ],
            storage_address,
            caller_address: tx_info.sender_address(),
            call_type: CallType::Call,

            initial_gas: remaining_gas_for_fee_transfer,
        };
        let mut context = EntryPointExecutionContext::new_invoke(
            tx_context,
            true,
            SierraGasRevertTracker::new(GasAmount(remaining_gas_for_fee_transfer)),
        );

        Ok(fee_transfer_call
            .execute(state, &mut context, &mut remaining_gas_for_fee_transfer)
            .map_err(|error| Box::new(TransactionFeeError::ExecuteFeeTransferError(error)))?)
    }
```

**File:** crates/blockifier_test_utils/resources/ERC20/ERC20_Cairo0/ERC20_without_some_syscalls/ERC20/ERC20.cairo (L77-106)
```text
@external
func transfer{syscall_ptr: felt*, pedersen_ptr: HashBuiltin*, range_check_ptr}(
    recipient: felt, amount: Uint256
) -> (success: felt) {
    let (sender) = get_caller_address();
    ERC20_transfer(sender, recipient, amount);

    return (TRUE,);
}

@external
func transferFrom{syscall_ptr: felt*, pedersen_ptr: HashBuiltin*, range_check_ptr}(
    sender: felt, recipient: felt, amount: Uint256
) -> (success: felt) {
    alloc_locals;
    let (local caller) = get_caller_address();
    let (local caller_allowance: Uint256) = ERC20_allowances.read(owner=sender, spender=caller);

    // Validates amount <= caller_allowance and returns TRUE if true.
    let (enough_allowance) = uint256_le(amount, caller_allowance);
    assert_not_zero(enough_allowance);

    ERC20_transfer(sender, recipient, amount);

    // Subtract allowance.
    let (new_allowance: Uint256) = uint256_sub(caller_allowance, amount);
    ERC20_allowances.write(sender, caller, new_allowance);

    return (TRUE,);
}
```
