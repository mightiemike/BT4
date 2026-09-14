### Title
Relayer fee is transferred unconditionally before the underlying ft_transfer/base-token action is confirmed to succeed, allowing fee extraction with no completed user transfer - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
In `inner_rlp_execute`, when a decoded Ethereum-emulated transaction is an `EOABaseTokenTransfer` or `ERC20Transfer` with a non-zero `fee`, the wallet contract immediately creates and dispatches a separate `promise_batch_action_transfer` to pay the relayer/predecessor, before the promise that actually performs the requested transfer (base-token transfer or `ft_transfer` function call) has even been scheduled, let alone confirmed successful. The return/outcome of the actual transfer action is only checked later, in `rlp_execute_callback`, and that check has no effect on the already-dispatched, independent fee-refund receipt.

### Finding Description
`inner_rlp_execute` parses the RLP transaction, and if the resulting `TransactionKind` is `EOABaseTokenTransfer` or `ERC20Transfer` with `fee != 0`, it unconditionally fires off the fee payment to `context.predecessor_account_id` as a standalone, independent action receipt: [1](#0-0) 

This fee-payment receipt is created and queued before the code below constructs the promise that actually performs the transfer/`ft_transfer` (and, for `ERC20Transfer`, before even checking whether the receiver is registered with the token contract via `storage_balance_of`): [2](#0-1) 

The only place where the outcome of the actual transfer promise is inspected is `rlp_execute_callback`, which checks `env::promise_result(0)` and, on `PromiseResult::Failed`, refunds only the `caller_deposit` (the attached NEAR deposit) — not the fee that was already paid out: [3](#0-2) 

This is structurally the same bug class as the reported "ignoring return value of `transfer()`" issue: a value-moving transfer (the fee payment) is dispatched without any dependency on, or later reconciliation with, the success/failure of the transfer it is supposed to compensate for. Because NEAR promises created via `env::promise_batch_create` on `context.predecessor_account_id` are independent receipts (not chained via `.then()` to the main action), they execute and finalize regardless of whether the main `ft_transfer` or base-token `Transfer` action later fails (e.g., insufficient token balance causing `ft_transfer` to panic, or `storage_balance_of`/`storage_deposit` failing for an unregistered ERC-20 receiver).

### Impact Explanation
A malicious or unaware relayer can submit a validly signed EOA/ERC-20 transfer transaction from the user's wallet contract where the specified `fee` is paid out to the relayer even though the underlying token or base-token transfer to the intended receiver ultimately fails (e.g., the wallet has insufficient token balance, the `ft_transfer` call panics, or the multi-step `storage_deposit` + `ft_transfer` promise chain fails). The nonce is incremented (preventing simple replay) and the fee has already left the user's control, but the intended value transfer to the receiver never completes. This results in unauthorized value movement: the user pays a fee for a transaction that produces no economic effect for the intended recipient, and the relayer receives payment for a service that was not actually rendered/successfully executed. This directly matches the "unauthorized value movement" acceptance criterion.

### Likelihood Explanation
This path is reachable by any account/relayer capable of calling `rlp_execute` on a deployed `WalletContract` with a validly signed Ethereum-emulated transaction, which is the wallet contract's primary, unprivileged entry point (`#[payable] pub fn rlp_execute`) as seen in [4](#0-3) . No validator, node, or operator privilege is required — only a user-signed Ethereum transaction plus a relayer/submitter transaction. The failure condition needed to trigger the discrepancy (e.g., `ft_transfer` reverting due to insufficient balance, or storage deposit/registration edge cases for `ERC20Transfer`) is a normal, easily reachable runtime condition, not an adversarial network/validator scenario, so likelihood is meaningful rather than purely theoretical.

### Recommendation
Do not dispatch the fee-payment transfer independently before the main action's outcome is known. Instead, chain the fee payment as a promise dependent on the success of the main transfer/`ft_transfer` action (e.g., include it as part of the batch/`.then()` chain that leads into `rlp_execute_callback`, and only execute the fee transfer inside the callback when `env::promise_result(0)` is `PromiseResult::Successful`). This ensures the relayer fee is paid only if the underlying user-intended transfer actually completes successfully, mirroring the fix pattern of checking a transfer's return/result before treating it as successful.

### Proof of Concept
1. Deploy a `WalletContract` for an eth-implicit account and fund it with a token balance smaller than the amount intended to be sent, but with enough deposited NEAR to cover a `fee`.
2. Construct and sign (with the wallet's Ethereum key) an RLP transaction representing an ERC-20 `transfer(to, value)` call where `value` exceeds the wallet's token balance, and set a non-zero relayer `fee` in the emulation metadata (per `ParsableEthEmulationKind::ERC20Transfer { receiver_id, fee }`, constructed as shown in [5](#0-4) ).
3. Have any relayer account call `rlp_execute` on the wallet contract with this transaction.
4. Observe: `inner_rlp_execute` immediately schedules and sends the `fee` to the relayer (`context.predecessor_account_id`) via `promise_batch_action_transfer` at [6](#0-5) , before the `ft_transfer` call chain (`storage_balance_of` → possibly `storage_deposit` → `ft_transfer`) is even constructed.
5. Because the wallet's token balance is insufficient, `ft_transfer` panics/fails on the token contract; `rlp_execute_callback` receives `PromiseResult::Failed` and only refunds the `caller_deposit` NEAR amount, not the fee, per [7](#0-6) .
6. Result: the relayer is paid the fee even though the receiver never received any tokens — the value transfer silently failed while the compensating fee transfer succeeded.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-128)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
        // To ensure user actions are executed in the desired order,
        // having multiple transactions in flight at the same time is
        // not allowed.
        if self.has_in_flight_tx {
            return PromiseOrValue::Value(ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(
                    "Error: transaction already in progress, please try again later.".into(),
                ),
            });
        }
        let current_account_id = env::current_account_id();
        let predecessor_account_id = env::predecessor_account_id();
        let result = inner_rlp_execute(
            current_account_id.clone(),
            predecessor_account_id,
            target,
            tx_bytes_b64,
            &mut self.nonce,
        );

        match result {
            Ok(promise) => {
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(Error::Relayer(_)) if env::signer_account_id() == current_account_id => {
                let promise = create_ban_relayer_promise(current_account_id);
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(e) => PromiseOrValue::Value(e.into()),
        }
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-316)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L433-458)
```rust
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs (L59-93)
```rust
        ERC20_TRANSFER_SELECTOR => {
            // We intentionally map to `u128` instead of `U256` because the NEP-141 standard
            // is to use u128.
            let (to, value): (Address, u128) =
                ethabi_utils::abi_decode(&ERC20_TRANSFER_SIGNATURE, &tx.data[4..])?;
            let receiver_id: AccountId = format!("0x{}{}", hex::encode(to), suffix)
                .parse()
                .unwrap_or_else(|_| env::panic_str("eth-implicit accounts are valid account ids"));

            // Include any data after the main args as a memo in the transfer.
            // The main data takes 68 bytes because there is a 4-byte selector followed
            // by two arguments which are each allocated 32 bytes according to the
            // Solidity ABI standard.
            let memo = if tx.data.len() > 68 {
                Some(format!(r#""0x{}""#, hex::encode(&tx.data[68..])))
            } else {
                None
            };
            let args = format!(
                r#"{{"receiver_id": "{}", "amount": "{}", "memo": {}}}"#,
                receiver_id.as_str(),
                value,
                memo.as_deref().unwrap_or("null"),
            );
            Ok((
                Action::FunctionCall {
                    receiver_id: target.to_string(),
                    method_name: "ft_transfer".into(),
                    args: args.into_bytes(),
                    gas: 2 * FIVE_TERA_GAS,
                    yocto_near: 1,
                },
                ParsableEthEmulationKind::ERC20Transfer { receiver_id, fee },
            ))
        }
```
