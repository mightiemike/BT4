### Title
Wallet Contract permanently absorbs relayer's attached deposit when NEP-141 storage deposit is not needed - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
`WalletContract::rlp_execute` is `#[payable]` and any external caller (a relayer) can attach an arbitrary NEAR deposit, which is tracked as a `CallerDeposit` for possible refund. That deposit is only ever refunded to the caller in the failure branch of `rlp_execute_callback` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:296-317`). When the ERC-20 emulation path finds the receiver already registered for NEP-141 storage (`nep_141_storage_balance_callback`'s `Some(_)` branch, `lib.rs:224-238`), the contract never spends the `NEP_141_STORAGE_DEPOSIT_AMOUNT` the relayer attached to cover a possible `storage_deposit` call, yet on success the deposit is never returned either. It is silently merged into the wallet (eth-implicit) account's own balance with no code path to reclaim it for the relayer, mirroring the "sends money but contract doesn't rule out/return the unneeded value" pattern in the report.

### Finding Description
1. `rlp_execute` accepts any `attached_deposit` from the predecessor (relayer) and `CallerDeposit::new` captures it whenever `predecessor_account_id != current_account_id`: [1](#0-0) 
2. For an ERC-20 (NEP-141) transfer, the wallet first checks `storage_balance_of` on the token, and only if the receiver is unregistered does it attach `NEP_141_STORAGE_DEPOSIT_AMOUNT` to a nested `storage_deposit` call: [2](#0-1) 
3. If the receiver is already registered (`Some(_)` branch), the contract goes straight to `action_to_promise` (the `ft_transfer` call, which only needs 1 yoctoNEAR) and then to `rlp_execute_callback`, **never spending** the attached deposit that was reserved for the possible `storage_deposit` call.
4. `rlp_execute_callback` only issues a refund transfer of the tracked `caller_deposit` in the `PromiseResult::Failed` branch; on `PromiseResult::Successful`, the deposit is not refunded at all: [3](#0-2) 
5. Because the attached deposit was already credited to the wallet contract's account balance the instant the `rlp_execute` receipt began execution (standard NEAR attached-deposit semantics), and because ETH-implicit accounts can never receive a full-access key (`AddKey` with `FullAccess` is rejected as `UnsupportedAction::AddFullAccessKey`) nor be deleted, there is no protocol-level mechanism for the relayer to recover this unconsumed deposit. It is permanently merged into the user's wallet balance instead of being returned to whoever paid it.

This is analogous to the reported `ExchangeProxy.executeSwap()` bug: value attached to cover a contingent code path is not tracked against what the contract actually consumes, and any excess is not returned when that contingency doesn't materialize — it is simply absorbed by the contract/account, with no path back to the sender.

### Impact Explanation
Any external relayer that overestimates or conservatively attaches `NEP_141_STORAGE_DEPOSIT_AMOUNT` (or any deposit) to cover a possible NEP-141 registration cost loses that value permanently whenever the receiver turns out to already be registered — a state that is entirely plausible/likely for any actively used token account. This is a direct, unauthorized value transfer from the relayer to the eth-implicit wallet account with no recovery path, which fits the "concrete unauthorized value movement / permanently frozen funds" criteria. Because relayers are expected by design to serve many wallets/many transactions (the entire incentive model in `inner_rlp_execute`'s fee-refund logic is built around relayers being economically compensated), a systemic mis-attachment of storage deposit funds directly erodes relayer economics at protocol scale.

### Likelihood Explanation
Likelihood is `Medium`: it requires an external relayer to attach a non-zero deposit intended for `NEP_141_STORAGE_DEPOSIT_AMOUNT` on an ERC-20 transfer whose receiver is already registered with the NEP-141 token — a state that is very common in practice (once a receiver account interacts with a fungible token once, it stays registered). The relayer is an "unprivileged" role reachable purely by observing an rlp-encoded Ethereum transaction and submitting a NEAR transaction with a chosen deposit amount; no validator or admin privilege is required.

### Recommendation
Track how much of the caller's attached deposit is actually consumed by nested cross-contract calls (e.g., by only reserving `NEP_141_STORAGE_DEPOSIT_AMOUNT` from the caller's deposit lazily, or by always issuing a refund transfer of any unused portion of `caller_deposit` back to the caller in `rlp_execute_callback`'s success branch as well as its failure branch), rather than refunding only on total failure.

### Proof of Concept
1. Relayer sends an ETH-encoded `transfer(to, value)` (ERC-20 selector `0xa9059cbb`) call through `rlp_execute`, attaching `NEP_141_STORAGE_DEPOSIT_AMOUNT` (1_250 * `MICRO_NEAR`) NEAR as `attached_deposit` to cover the possibility that `to` is unregistered with the NEP-141 token.
2. `inner_rlp_execute` -> `parse_rlp_tx_to_action` classifies this as `EthEmulationKind::ERC20Transfer`; `CallerDeposit::new` records the relayer's full attached deposit for potential refund (`types.rs:180-192`).
3. The wallet issues `storage_balance_of(receiver_id)` on the token, then in `nep_141_storage_balance_callback` (`lib.rs:194-273`) finds `Some(_)` (receiver already registered), so it calls `action_to_promise` directly to run `ft_transfer` with a 1-yoctoNEAR deposit, skipping the `storage_deposit` call entirely.
4. `rlp_execute_callback` receives `PromiseResult::Successful` and returns `ExecuteResponse{success:true, ...}` without ever transferring back the ~1_250 * `MICRO_NEAR` deposit the relayer attached (`lib.rs:296-316`).
5. The relayer's attached deposit remains part of the eth-implicit account's balance permanently; the relayer has no way to recoup it through this contract's API surface (no full-access key can ever be added: `types.rs`/`Error::User(UserError::UnsupportedAction(UnsupportedAction::AddFullAccessKey))`).

Note: I could not find a corresponding integration test that exercises this exact "success + no `storage_deposit` needed" case to directly confirm the balance delta at runtime (the existing `test_caller_refunds` test in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs:170-229` only covers the plain failure-refund and address-registrar "tokens are spent" cases, not the ERC-20/NEP-141 storage-deposit skip path). This should be validated with a dedicated integration test using a mocked NEP-141 token that returns `Some` from `storage_balance_of`.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-192)
```rust
impl CallerDeposit {
    pub fn new(context: &ExecutionContext) -> Option<Self> {
        // Only track for external (non-self) callers
        if context.current_account_id == context.predecessor_account_id {
            return None;
        }

        NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
            account_id: context.predecessor_account_id.clone(),
            yocto_near,
        })
    }
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L224-269)
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
