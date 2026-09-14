### Title
Wallet Contract pays the relayer fee unconditionally, even if the underlying eth-emulated transfer fails - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
In `inner_rlp_execute`, when a `TransactionKind::EthEmulation(EOABaseTokenTransfer { fee, .. })` or `ERC20Transfer { fee, .. }` is parsed, the contract immediately schedules an independent promise that transfers `fee` yoctoNEAR to the relayer (`predecessor_account_id`), *before* the actual action (the base-token `Transfer` or the ERC-20 `ft_transfer`) is even attempted: [1](#0-0) 
The action that fulfils the user's intended transfer is dispatched separately, as its own promise chain ending in `rlp_execute_callback`, which only surfaces success/failure in the returned `ExecuteResponse`: [2](#0-1) 
The relayer's fee-refund promise and the user-action promise are two unrelated promise batches; they are not chained with `.then()`/`.and()` so that the fee only executes conditionally on the action succeeding.

### Finding Description
This mirrors the analog bug class from the external report: a balance deduction (`pocket_money_balance` in the Hats finding; here, the wallet's NEAR balance paid to the relayer) is finalized regardless of whether the corresponding "service" (a successful transfer/call) actually completed.

Concretely:
1. A relayer calls `rlp_execute` with an RLP-encoded, user-signed Ethereum-style transaction targeting a base-token transfer or ERC-20 transfer that includes a non-zero `fee` for the relayer, as computed in `parse_rlp_tx_to_action`: [3](#0-2) 
2. As soon as parsing succeeds, `inner_rlp_execute` unconditionally creates and dispatches a `promise_batch_action_transfer` of `fee` to the relayer: [4](#0-3) 
3. Separately, the actual action (base-token `Transfer` action, possibly gated by an address-registrar lookup, or the ERC-20 `ft_transfer` flow) is dispatched via its own independent promise/callback chain: [2](#0-1) 
4. Because these are two independent top-level promises rather than one chained promise, if the intended transfer/`ft_transfer` action itself fails (e.g., `PromiseResult::Failed` handled in `rlp_execute_callback`): [5](#0-4) 
the relayer's fee transfer still succeeds and is not rolled back, unlike the caller's own deposit which is explicitly refunded only in the `PromiseResult::Failed` branch above via `caller_deposit`.

The comment at the fee-creation site ("Users should always verify the fee before signing... Relayers should also verify the fee before sending") acknowledges the fee is paid on send, not on success, suggesting this is an intentional design trade-off rather than an oversight. However, this still means: whenever the wallet-owner-signed transaction's target action fails for any reason outside the user's/relayer's control (target account state changes between signing and execution, insufficient gas for the inner call, target contract logic reverting, storage-deposit/registration races on NEP-141), the wallet's NEAR balance is decremented and paid to the relayer with no compensating benefit delivered to the wallet owner — an analog to "decrease in balance even if transfer failed."

### Impact Explanation
Value moves out of the wallet-contract account (to the relayer) without the corresponding user-intended action taking effect. Because `rlp_execute` can be invoked by any relayer holding a `FunctionCall` access key scoped to `rlp_execute` (a role reachable by ordinary account holders/relayer operators, not requiring the wallet owner's full-access key), a relayer that races a target's state (e.g., de-registering NEP-141 storage, or submitting when the target action is guaranteed to fail) can repeatedly collect the fee while the user's intended transfer never lands. This is unauthorized value movement from the perspective of the wallet owner: they pay for a service that was never rendered.

### Likelihood Explanation
Moderate. It requires either (a) a legitimately signed user transaction whose target action fails at execution time due to conditions changing between signing and execution (plausible and not attacker-controlled), or (b) a relayer deliberately choosing to submit a transaction it can predict/engineer to fail on the target side (e.g., insufficient forwarded gas to the inner call, or targeting an account/method known to revert) while still collecting the fee. The contract's own architecture (multi-step promise chains for ERC-20/address-check flows) increases the number of ways the inner action can fail independently of the fee payment.

### Recommendation
Chain the relayer fee payment as a dependent step (via `.then()`/callback) on the success of the actual user action, or defer/batch the fee transfer into the same promise/receipt as the action so it only executes if the action succeeds; alternatively, refund the fee back to the wallet if `rlp_execute_callback` observes `PromiseResult::Failed` for the primary action, symmetric to how `caller_deposit` refunds already work.

### Proof of Concept
1. Deploy a `WalletContract` for an eth-implicit account funded with NEAR.
2. Sign (as the wallet owner) an Ethereum-style transaction representing an ERC-20 `ft_transfer` (or base-token transfer) to a target account, with `max_fee_per_gas * gas_limit` computed to a non-zero `fee`, per `parse_rlp_tx_to_action`.
3. Have a relayer call `rlp_execute` with this transaction where the target's `ft_transfer` (or transfer) is guaranteed to fail at execution time (e.g., target has since become an account with insufficient storage registration and the flow that would call `storage_deposit` runs out of allotted gas, or the target contract explicitly panics for that call).
4. Observe: `rlp_execute_callback` reports `success: false` for the intended action outcome, yet the relayer's `NearToken` balance increases by `fee` and the wallet's balance decreases by `fee`, because the fee-transfer promise created in `inner_rlp_execute` (lines 381-384) executed and completed independently of the failing target-action promise chain.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-317)
```rust
        match env::promise_result(0) {
            PromiseResult::Failed => {
                // The cross-contract call failed, refund the caller if needed
                if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
                    let refund_promise = env::promise_batch_create(&account_id);
                    env::promise_batch_action_transfer(
                        refund_promise,
                        NearToken::from_yoctonear(yocto_near.into()),
                    );
                }

                ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Failed Near promise".into()),
                }
            }
            PromiseResult::Successful(value) => {
                ExecuteResponse { success: true, success_value: Some(value), error: None }
            }
        }
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L374-385)
```rust
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                fee,
                ..
            })
            | TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { fee, .. }) =
                &transaction_kind
            {
                if !fee.is_zero() && context.predecessor_account_id != context.current_account_id {
                    let refund_promise = env::promise_batch_create(&context.predecessor_account_id);
                    env::promise_batch_action_transfer(refund_promise, *fee);
                }
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-470)
```rust
    let promise = match transaction_kind {
        TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
            address_check: Some(address),
            ..
        }) => {
            let callback_gas = ADDRESS_CHECK_CALLBACK_GAS.saturating_add(action.gas());
            let ext = WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let address_registrar = {
                let account_id = ADDRESS_REGISTRAR_ACCOUNT_ID
                    .trim()
                    .parse()
                    .unwrap_or_else(|_| env::panic_str("Invalid address registrar"));
                ext_registrar::ext(account_id).with_static_gas(REGISTRAR_LOOKUP_GAS)
            };
            let address = format!("0x{}", hex::encode(address));
            address_registrar.lookup(address).then(ext.address_check_callback(
                target,
                action,
                caller_deposit,
            ))
        }
        TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { receiver_id, .. }) => {
            // In the case of the emulated ERC-20 transfer, the receiving account
            // might not be registered with the NEP-141 contract (per the NEP-145)
            // storage standard. Therefore we must create a multi-step promise where
            // first we check if the receiver is registered and then if not call
            // `storage_deposit` in addition to `ft_transfer`.
            let token_id = target;
            let callback_gas = NEP_141_STORAGE_BALANCE_CALLBACK_GAS.saturating_add(action.gas());
            let ext: WalletContractExt =
                WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let storage_balance_args =
                format!(r#"{{"account_id": "{}"}}"#, receiver_id.as_str()).into_bytes();
            Promise::new(token_id.clone())
                .function_call(
                    "storage_balance_of".into(),
                    storage_balance_args,
                    NearToken::from_yoctonear(0),
                    NEP_141_STORAGE_BALANCE_OF_GAS,
                )
                .then(ext.nep_141_storage_balance_callback(
                    token_id,
                    receiver_id,
                    action,
                    caller_deposit,
                ))
        }
        TransactionKind::EthEmulation(EthEmulationKind::SelfBaseTokenTransfer) => {
            // Base token transfers to self are no-ops on Near, so we do not need to
            // schedule an additional call. We can simply go straight to `rlp_execute_callback`.
            let ext: WalletContractExt =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            ext.rlp_execute_callback(caller_deposit)
        }
        _ => {
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            action_to_promise(target, action)?.then(ext.rlp_execute_callback(caller_deposit))
        }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L54-64)
```rust
    // Compute the fee based on the user's Ethereum transaction.
    // This is sent as a refund to the relayer in the case of an emulated base token
    // transfer or ERC-20 transfer. The reason for this refund is that it allows a
    // user with $NEAR to use a relayer service from their wallet immediately without
    // additional on-boarding.
    let tx_fee = {
        // Limit the cost by `VALUE_MAX` since we will convert this to a $NEAR amount.
        // The call to `low_u128` is safe because `VALUE_MAX` is the largest accepted value.
        let wei_amount = tx.max_fee_per_gas.saturating_mul(tx.gas_limit).min(VALUE_MAX).low_u128();
        NearToken::from_yoctonear(wei_amount.saturating_mul(MAX_YOCTO_NEAR as u128))
    };
```
