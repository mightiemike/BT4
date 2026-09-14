Confirmed: `try_into_near_action`'s `additional_value` comes purely from the RLP-encoded Ethereum `tx.value` field (`internal.rs:162-163`), which is entirely independent of `env::attached_deposit()` (the NEAR-native deposit attached to the `#[payable] rlp_execute` call itself). For `Action::AddKey`/`Action::DeleteKey`, `try_into_near_action` never uses `additional_value` at all (`types.rs:262-296`), and `action_to_promise` builds `AddKeyAction`/`DeleteKeyAction` promises that carry zero deposit (`lib.rs:484-499`). Meanwhile any NEAR attached to the call is unconditionally credited to the wallet contract's balance the moment `rlp_execute` (payable) is invoked. `CallerDeposit` is only refunded when the inner promise result is `Failed` (`lib.rs:296-311`); on `Successful` (which is exactly what happens for AddKey/DeleteKey, since those actions have no failure-prone external dependency) the caller's deposit is silently kept by the contract with no refund and no use.

### Title
Attached NEAR deposit is permanently locked when `rlp_execute` performs an `AddKey`/`DeleteKey` action - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` is marked `#[payable]`, so any NEAR attached by an external caller (a relayer or any account submitting the transaction/`FunctionCall` with a deposit) is deposited into the wallet contract's balance as soon as the call is made. The contract only refunds this attached deposit to the external caller (`CallerDeposit`) when the inner cross-contract promise it schedules subsequently fails. For `AddKey` and `DeleteKey` actions — which never require or consume any deposit — the inner promise generally succeeds, so the attached deposit is never refunded and is permanently absorbed by the wallet contract with no corresponding benefit to the caller.

### Finding Description
`rlp_execute` is `#[payable]` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:88-89`), meaning `env::attached_deposit()` is credited to the wallet contract's balance unconditionally by the runtime the moment the method executes, regardless of what action is ultimately performed.

`inner_rlp_execute` computes `caller_deposit = CallerDeposit::new(&context)` (`lib.rs:345`), tracking the deposit only so it can potentially be refunded later: [1](#0-0) 

The action itself is derived from the RLP-encoded Ethereum transaction's `value` field via `try_into_near_action`, which is a completely separate value from the NEAR-native `attached_deposit`: [2](#0-1) 

For `AddKey`/`DeleteKey` actions, `try_into_near_action` ignores `additional_value` entirely, and the resulting Near action types (`AddKeyAction`, `DeleteKeyAction`) carry no deposit field at all: [3](#0-2) [4](#0-3) 

The only place the caller's deposit is ever returned is inside `rlp_execute_callback`, and only on the `Failed` branch: [5](#0-4) 

Since `AddKey`/`DeleteKey` promises target the wallet contract's own account and require no external dependency to succeed, they will normally resolve as `Successful`, so `caller_deposit` is simply dropped — the deposit is neither forwarded to any receiver nor refunded to the caller. This matches the reported bug class exactly: a `payable`-style entry point has code paths (`AddKey`/`DeleteKey`) that never need the attached value, yet nothing prevents or refunds that value, so it is permanently absorbed by the contract.

### Impact Explanation
Any external account (a relayer forwarding a user's signed Ethereum-style transaction, or any account directly calling `rlp_execute`) that attaches a NEAR deposit while the encoded action is `AddKey` or `DeleteKey` will have that deposit irrecoverably locked into the wallet contract's balance. This is a genuine, permanent loss of funds for the caller with no compensating on-chain accounting distinguishing it from legitimate contract balance, satisfying the "permanently frozen funds" criterion.

### Likelihood Explanation
Likelihood is moderate: it requires a caller to attach a nonzero deposit to a call whose parsed action is `AddKey`/`DeleteKey` (i.e., `TransactionKind::NearNativeAction`/`SelfNearNativeAction` path with a non-zero `attached_deposit` but action type that never spends it). This could occur accidentally (e.g. a relayer or SDK integration mis-attaching a deposit meant to cover another purpose) or could be deliberately triggered by a griefer forcing a third party's relayer/wallet flow to burn a deposit, since `rlp_execute` is a public, unprivileged, permissionless entry point reachable from any transaction sender.

### Recommendation
Track any non-zero `attached_deposit` uniformly regardless of the promise's success/failure outcome for actions (`AddKey`, `DeleteKey`, and any other action variant) that structurally cannot consume a deposit, and always refund the caller for the portion of the deposit not actually forwarded/spent by the resulting Near action — rather than only refunding on `PromiseResult::Failed`. Alternatively, reject `rlp_execute` calls upfront (return an error before scheduling the promise) if `env::attached_deposit() > 0` and the parsed action is `AddKey` or `DeleteKey`, analogous to the `require(0 == msg.value)` recommendation in the original report.

### Proof of Concept
1. Deploy `WalletContract` for an eth-implicit account and have it own an access key that permits calling `rlp_execute`.
2. Craft/sign an RLP Ethereum transaction whose calldata matches `ADD_KEY_SELECTOR` (or `DELETE_KEY_SELECTOR`) with a valid, well-formed public key — see `parse_tx_data` in `internal.rs:272-304`.
3. Call `rlp_execute(target, tx_bytes_b64)` as any external predecessor account (`predecessor_account_id != current_account_id`) and attach a nonzero NEAR deposit (e.g. 1 NEAR) to the call.
4. `inner_rlp_execute` creates `caller_deposit = Some(CallerDeposit { account_id: predecessor, yocto_near: attached })` and schedules `action_to_promise` → `Promise::new(target).add_access_key_allowance_with_nonce(...)` (or `.delete_key(...)`), followed by `.then(rlp_execute_callback(caller_deposit))`.
5. The `AddKey`/`DeleteKey` promise succeeds (no external dependency), so `rlp_execute_callback` hits the `PromiseResult::Successful` branch, which returns `ExecuteResponse{success:true,...}` without ever refunding `caller_deposit`.
6. Observe the caller's account balance is now permanently reduced by the attached deposit, while the wallet contract's balance increased by the same amount with no explicit or contract-level mechanism for the caller to reclaim it.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-191)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L262-296)
```rust
            Action::AddKey {
                public_key_kind,
                public_key,
                nonce,
                is_full_access,
                is_limited_allowance,
                allowance,
                receiver_id,
                method_names,
            } => {
                let public_key = construct_public_key(public_key_kind, &public_key)?;
                let access_key = if is_full_access {
                    AccessKey { nonce, permission: AccessKeyPermission::FullAccess }
                } else {
                    let allowance = if is_limited_allowance { Some(allowance) } else { None };
                    AccessKey {
                        nonce,
                        permission: AccessKeyPermission::FunctionCall(FunctionCallPermission {
                            allowance: allowance.map(NearToken::from_yoctonear),
                            receiver_id: receiver_id
                                .parse()
                                .map_err(|_| Error::User(UserError::InvalidAccessKeyAccountId))?,
                            method_names,
                        }),
                    }
                };
                let action = AddKeyAction { public_key, access_key };
                near_action::Action::AddKey(action)
            }
            Action::DeleteKey { public_key_kind, public_key } => {
                let action = DeleteKeyAction {
                    public_key: construct_public_key(public_key_kind, &public_key)?,
                };
                near_action::Action::DeleteKey(action)
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L159-165)
```rust
    validate_tx_value(&tx)?;

    // Call to `low_u128` here is safe because of the validation done in `validate_tx_value`
    let near_action = action
        .try_into_near_action(tx.value.raw().low_u128().saturating_mul(MAX_YOCTO_NEAR.into()))?;

    Ok((near_action, transaction_kind))
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L484-499)
```rust
        near_action::Action::AddKey(action) => match action.access_key.permission {
            near_action::AccessKeyPermission::FullAccess => {
                Err(Error::User(UserError::UnsupportedAction(UnsupportedAction::AddFullAccessKey)))
            }
            near_action::AccessKeyPermission::FunctionCall(access) => Ok(Promise::new(target)
                .add_access_key_allowance_with_nonce(
                    action.public_key,
                    access.allowance.and_then(Allowance::limited).unwrap_or(Allowance::Unlimited),
                    access.receiver_id,
                    access.method_names.join(","),
                    action.access_key.nonce,
                )),
        },
        near_action::Action::DeleteKey(action) => {
            Ok(Promise::new(target).delete_key(action.public_key))
        }
```
