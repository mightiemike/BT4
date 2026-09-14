### Title
Wallet Contract treats NEP-141 `ft_transfer` as successful based solely on the cross-contract call not panicking, without validating token-transfer semantics, allowing nonce consumption and relayer fee payment for a transfer that silently failed - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
`WalletContract::rlp_execute_callback` determines whether an emulated ERC-20/NEP-141 transfer "succeeded" purely by checking whether the promise result is `PromiseResult::Successful` (i.e., the callee did not panic), never inspecting the semantic outcome of the underlying `ft_transfer` call. [1](#0-0)  This mirrors the ERC20 report's root cause: an interface that can indicate failure without reverting/panicking is trusted as success, while the caller's bookkeeping (nonce, relayer fee) is already advanced irreversibly.

### Finding Description
In `inner_rlp_execute`, the nonce is incremented before the outcome of the actual action (including the emulated `ft_transfer`) is known, based only on parsing succeeding, not on the transfer's actual on-chain result. [2](#0-1)  Additionally, for `EthEmulationKind::ERC20Transfer` with a non-zero fee, a completely independent promise is dispatched immediately to pay the relayer, decoupled from any chained dependency on whether the `ft_transfer` promise itself succeeds. [3](#0-2) 

The emulated ERC20 transfer is executed by mapping `Action::FunctionCall` to `ft_transfer` on the target NEP-141 token contract. [4](#0-3)  After the storage-balance check, the actual transfer is dispatched and chained to `rlp_execute_callback`. [5](#0-4)  That callback's only success criterion is `PromiseResult::Successful(value)`, unconditionally reporting `success: true` regardless of the content of `value` or whether the token contract's `ft_transfer` logic actually decremented/incremented balances as expected. [1](#0-0) 

This is the direct analog of the ERC20 audit finding: just as a non-standard ERC20 `transfer` can return `false` instead of reverting (causing `FootiumPrizeDistributor` to mark a claim as complete despite no tokens moving), a non-standard or buggy NEP-141 token contract can return normally (not panic) from `ft_transfer` without actually moving balances. Because the Wallet Contract's callback equates "did not panic" with "transfer succeeded," and because the nonce increment and relayer fee payment already occurred independently of this outcome, the end-user's transaction is consumed (nonce burned, fee paid to relayer) while the intended token transfer silently fails.

### Impact Explanation
If a target NEP-141 token contract does not strictly panic on all transfer failure conditions (e.g., a buggy or intentionally malicious token, or one with non-standard semantics), the Wallet Contract will report `success: true` and irreversibly consume the user's nonce and pay the relayer's fee, even though the user's tokens never moved. This is unauthorized value loss to the wallet owner (fee paid + nonce consumed for no effect) and a discrepancy between the recorded `ExecuteResponse` and actual on-chain token balances, matching the "concrete unauthorized value movement" / "permanently frozen" bar since the nonce-consumption is irreversible and the transaction cannot be resubmitted.

### Likelihood Explanation
Reachable by any unprivileged actor: a relayer (or the wallet owner) submits a signed Ethereum-style transaction via `rlp_execute` targeting an ERC20-emulated NEP-141 token whose contract can return without panicking on a failed transfer path. [6](#0-5)  No special privileges are required beyond normal use of the wallet contract's public flow.

### Recommendation
`rlp_execute_callback` (and the NEP-141-specific paths) should not treat "promise not failed" as equivalent to "transfer succeeded." For NEP-141 transfers specifically, decode and validate the expected side effects (e.g., verify balances via an explicit follow-up `ft_balance_of` check, or require token contracts used with the emulation layer to strictly conform to the NEP-141 panic-on-failure requirement) before considering the emulated ERC20 transfer successful, and make the relayer fee payment/nonce-finalization dependent on that confirmed outcome rather than being dispatched unconditionally.

### Proof of Concept
1. Deploy a NEP-141-like token contract whose `ft_transfer` returns normally (no panic) when the sender's balance is insufficient, instead of aborting per the NEP-141 standard.
2. A relayer submits a signed RLP transaction via `rlp_execute` on the Wallet Contract targeting an ERC20-emulated transfer to this token, with a non-zero fee. [7](#0-6) 
3. The independent relayer-fee-refund promise executes regardless of the transfer's eventual outcome. [3](#0-2) 
4. The `ft_transfer` call returns without panicking despite failing internally; `rlp_execute_callback` observes `PromiseResult::Successful` and reports `success: true`. [8](#0-7) 
5. The nonce was already incremented in step 2's parsing phase, so the transaction cannot be replayed. [9](#0-8)  The wallet owner has paid the relayer fee and consumed a nonce for a transfer that never happened.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L224-273)
```rust
        let promise = match maybe_storage_balance {
            Some(_) => {
                // receiver_id is registered so we can send the transfer
                // without additional actions. Note: in the standard NEP-141
                // implementation it is impossible to have `Some` storage balance,
                // but have it be insufficient to transact.
                match action_to_promise(token_id, action)
                    .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
                {
                    Ok(p) => p,
                    Err(e) => {
                        return PromiseOrValue::Value(e.into());
                    }
                }
            }
            None => {
                // receiver_id is not registered so we must call `storage_deposit` first.
                let storage_deposit_args =
                    format!(r#"{{"account_id": "{receiver_id}"}}"#).into_bytes();
                let transfer_function_call = match action {
                    near_action::Action::FunctionCall(x) => x,
                    _ => {
                        return PromiseOrValue::Value(ExecuteResponse {
                            success: false,
                            success_value: None,
                            error: Some(
                                "Expected function call action to perform NEP-141 transfer".into(),
                            ),
                        });
                    }
                };
                Promise::new(token_id)
                    .function_call(
                        "storage_deposit".into(),
                        storage_deposit_args,
                        NEP_141_STORAGE_DEPOSIT_AMOUNT,
                        NEP_141_STORAGE_DEPOSIT_GAS,
                    )
                    .function_call(
                        transfer_function_call.method_name,
                        transfer_function_call.args,
                        transfer_function_call.deposit,
                        transfer_function_call.gas,
                    )
                    .then(ext.rlp_execute_callback(caller_deposit))
            }
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
```

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L349-365)
```rust
        Ok((action, transaction_kind)) => {
            // Increment nonce for all cases where the registrar contract is not needed
            // to prevent replay of those transactions. For transactions that go through
            // the registrar we still do not know if the transaction has a relayer error
            // or not, therefore we must delay incrementing the nonce.
            //
            // Note: relayers with access keys cannot use this delay to needlessly spend
            // the users tokens because only one transaction is allowed to be in-flight
            // at a time.
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                address_check: Some(_),
                ..
            }) = &transaction_kind
            {
            } else {
                *nonce = nonce.saturating_add(1);
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
