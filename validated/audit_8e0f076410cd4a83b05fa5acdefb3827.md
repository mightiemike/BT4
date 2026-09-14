## Analog Found: Unconditional Relayer Fee Payment Independent of Transfer Success in Wallet Contract

### Title
Relayer fee is paid even when the underlying NEP-141/base-token transfer fails - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The Sherlock report describes `UXDController` trusting an ERC20 `transferFrom`/`transfer` call's implicit success (no `safeTransfer`), so a token that returns `false` instead of reverting lets the protocol believe a transfer happened when it did not, causing loss of funds. The nearcore analog is in the `near-wallet-contract` (Aurora eth-implicit wallet), where the relayer-fee refund promise is dispatched unconditionally, independent from whether the promise executing the user's actual intended action (an NEP-141 `ft_transfer`, ERC-20 emulation, or base-token transfer) succeeds.

### Finding Description
In `inner_rlp_execute`, when the parsed transaction is an `EOABaseTokenTransfer` or `ERC20Transfer` with a non-zero `fee`, the contract immediately creates an independent promise batch that transfers the fee to the relayer (`env::promise_batch_action_transfer`), before it ever creates or schedules the promise that performs the user's actual action: [1](#0-0) 

This fee-refund promise is not chained to (`.then(...)`) or otherwise made dependent on the outcome of the action promise built later in the same function (`action_to_promise(...)`/`nep_141_storage_balance_callback`), which is only checked afterwards in `rlp_execute_callback`: [2](#0-1) [3](#0-2) 

Because both promises are dispatched from the same receipt but are not causally linked, the relayer fee will be transferred out of the wallet's balance regardless of whether the actual `ft_transfer`/ERC-20/base-token action later fails (e.g. `PromiseResult::Failed` in `rlp_execute_callback`, line 297). This mirrors the ERC20 "no safeTransfer" bug class: value is moved based on an assumption of success rather than a verified outcome of the paired operation.

### Impact Explanation
A malicious or careless relayer (an unprivileged party who only needs to submit the outer NEAR transaction wrapping the user's signed RLP payload) can construct or select a scenario where the fee-bearing action is guaranteed to fail (e.g., insufficient token balance, insufficient attached gas for the inner cross-contract call, or malformed downstream call) while the unconditional fee-refund promise still executes successfully. The wallet's NEAR balance is drained to pay the relayer even though the user's transfer never completes — a direct, unauthorized value movement out of the wallet account with no offsetting benefit to the user, i.e., loss of funds. This satisfies the "unauthorized value movement" acceptance bar.

### Likelihood Explanation
The comment in the code itself acknowledges the very risk: "Users should always verify the fee before signing... Relayers should also verify the fee before sending to make sure the user's signed transaction will refund enough to cover the relayer's gas costs." This shows the fee payment is already known to be decoupled from the action's success by design, i.e., it is trivially reachable by any relayer submitting a `rlp_execute` transaction with a non-zero fee, without requiring any privileged role, validator collusion, or network-level manipulation. The only "cost" to a griefing relayer is forfeited gas, which is far outweighed by the fee received on a guaranteed-fail transfer.

### Recommendation
Chain the relayer fee-refund promise to the outcome of the primary action (e.g., via `.then(...)` off the same promise chain, releasing the fee only from within `rlp_execute_callback` after confirming `PromiseResult::Successful`), so that funds only move to the relayer when the user's intended action actually completes — analogous to using a "safe" transfer pattern that checks the real result before crediting value.

### Proof of Concept
1. A relayer receives (or self-crafts) a signed RLP transaction for a `ft_transfer`/ERC-20 emulation with `fee > 0`.
2. The relayer submits `rlp_execute` with attached gas/parameters (or waits for a state where the sender's token balance is insufficient) such that the `ft_transfer`/action promise created in `action_to_promise(...)`/`nep_141_storage_balance_callback` will fail.
3. `inner_rlp_execute` unconditionally issues the fee transfer to the relayer at lines 382-384 before the failing action promise resolves.
4. `rlp_execute_callback` later observes `PromiseResult::Failed` (line 297) and reports failure to the caller, but the relayer has already been paid — the wallet balance is permanently reduced with no successful transfer performed. [4](#0-3)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L275-317)
```rust
    #[private]
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();

        if n == 0 {
            // `rlp_execute_callback` is called directly in the case of an emulated self-transfer.
            return ExecuteResponse { success: true, success_value: None, error: None };
        } else if n > 1 {
            return ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(format!(
                    "Invariant violation: this callback comes after a single promise. n={n}"
                )),
            };
        }

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L366-385)
```rust

            // If the action is an emulated base token or ERC-20 transfer with a non-zero fee then
            // create a promise to send the refund to the relayer. This allows any relayer
            // to safely serve base token transfers from any wallet without additional
            // on-boarding because the relayer will receive some compensation for sending
            // the transaction. Users should always verify the fee before signing a base token
            // transfer. Relayers should also verify the fee before sending to make sure the
            // user's signed transaction will refund enough to cover the relayer's gas costs.
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-471)
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
    };
```
