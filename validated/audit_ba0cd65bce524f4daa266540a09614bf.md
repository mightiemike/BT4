## Title
Wallet Contract loses (permanently locks) an external caller's attached deposit when a multi-step promise chain fails before reaching the final refund callback - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The `WalletContract`'s `rlp_execute` entrypoint is `#[payable]` and accepts an attached deposit from an external (non-self) caller, which is captured as a `CallerDeposit` to be refunded if the underlying cross-contract action fails. [1](#0-0) [2](#0-1)  That refund, however, is only issued from one place: the `PromiseResult::Failed` branch of `rlp_execute_callback`. [3](#0-2)  Several earlier failure branches in the intermediate callbacks (`address_check_callback`, `nep_141_storage_balance_callback`) return an error `ExecuteResponse` directly without ever forwarding execution to `rlp_execute_callback`, silently dropping the `caller_deposit` parameter they were given. The deposit remains held by the wallet contract with no code path to reclaim it.

### Finding Description
`inner_rlp_execute` builds a `caller_deposit` from the attached deposit whenever the predecessor differs from the wallet's own account, and threads it through the promise chain so the final step can refund it on failure. [4](#0-3) 

For two of the transaction kinds, the promise chain routes through an intermediate callback before reaching `rlp_execute_callback`:
- `EOABaseTokenTransfer` with an `address_check` calls the address registrar, then `address_check_callback` [5](#0-4) 
- `ERC20Transfer` calls `storage_balance_of` on the token, then `nep_141_storage_balance_callback` [6](#0-5) 

Both intermediate callbacks receive `caller_deposit: Option<CallerDeposit>` as a parameter, but in their failure branches they return `PromiseOrValue::Value(ExecuteResponse{ success:false, ... })` directly, without transferring the deposit back and without forwarding it to any subsequent refund step:
- Registrar lookup failed: [7](#0-6) 
- Registrar response deserialize error: [8](#0-7) 
- "Invalid target" case (named account exists, non-owner signer): [9](#0-8) 
- NEP-141 `storage_balance_of` call failed: [10](#0-9) 
- NEP-141 response deserialize error: [11](#0-10) 
- Non-FunctionCall action mismatch: [12](#0-11) 

In every one of these branches the caller's attached NEAR deposit was already credited to the wallet contract's balance at the time `rlp_execute` was called (it is `#[payable]`), yet no transfer back to `caller_deposit.account_id` is ever scheduled. Since `WalletContract` exposes no owner/admin withdrawal function and the deposit was never associated with a durable pending-refund record in persisted state, these funds become permanently unrecoverable by the caller or anyone else — directly analogous to the reported `DirectBuyIssuer` issue where escrowed funds became inaccessible because the refund/return logic did not cover every code path that could consume the deposit.

### Impact Explanation
Any external relayer/caller who attaches a NEAR deposit when invoking `rlp_execute` (e.g., to compensate for a `storage_deposit` fee on an ERC-20-emulated transfer, or while going through the eth-implicit-address registrar check) permanently loses that deposit whenever the registrar lookup or NEP-141 `storage_balance_of` call fails, or returns an unparsable response, or the target resolves to an existing named account. This is unauthorized, permanent loss of user funds with no recovery path — a concrete "permanently frozen funds" outcome reachable purely from an unprivileged external caller's single transaction (no validator/operator/network-layer involvement).

### Likelihood Explanation
The registrar/NEP-141 external calls in these intermediate callbacks are cross-contract calls to accounts not controlled by the wallet owner or the protocol (address registrar contract, arbitrary NEP-141 token contracts). Such calls can plausibly fail (contract paused, method removed, gas exhaustion, malformed/incompatible response format, or simply the registrar contract being temporarily unavailable) — none of which require malicious behavior by the caller, making this readily triggerable in normal operation, not just adversarial conditions.

### Recommendation
Ensure every failure branch that currently returns `PromiseOrValue::Value(...)` directly from `address_check_callback` and `nep_141_storage_balance_callback` also performs the same caller-deposit refund logic used in `rlp_execute_callback` (i.e., schedule a `promise_batch_action_transfer` to `caller_deposit.account_id` for `caller_deposit.yocto_near`) before returning the failure response, or centralize refund handling so all early-exit paths route through a single refund-then-respond helper.

### Proof of Concept
1. An external relayer account (not the wallet's own eth-implicit account) calls `rlp_execute(target, tx_bytes_b64)` with an attached deposit, where the decoded transaction is an `ERC20Transfer` to an unregistered receiver (`inner_rlp_execute` → `ERC20Transfer` branch, `lib.rs:433-458`).
2. `CallerDeposit::new` captures the attached deposit since predecessor ≠ current account (`types.rs:180-191`).
3. The promise calls `storage_balance_of` on the token contract; suppose the token contract is temporarily failing or returns a value the wallet cannot deserialize.
4. `nep_141_storage_balance_callback` hits the `PromiseResult::Failed` (`lib.rs:203-210`) or deserialize-error (`lib.rs:211-220`) branch and returns `ExecuteResponse{success:false,...}` directly — the `caller_deposit` parameter is discarded without any transfer.
5. The relayer's attached deposit remains in the wallet contract's balance permanently; there is no method on `WalletContract` to withdraw or reclaim it.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L172-191)
```rust
/// A data type to keep track of the deposit given by an external caller.
/// This allows us to refund the caller's deposit if the cross-contract call fails.
#[derive(Debug, PartialEq, Eq, Clone, serde::Serialize, serde::Deserialize)]
pub struct CallerDeposit {
    pub account_id: AccountId,
    pub yocto_near: NonZeroU128,
}

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-148)
```rust
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L149-158)
```rust
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from account registrar".into()),
                    });
                }
            },
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L165-173)
```rust
            if env::signer_account_id() == current_account_id {
                create_ban_relayer_promise(current_account_id)
            } else {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Invalid target: target is address corresponding to existing named account_id".into()),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-210)
```rust
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L211-220)
```rust
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from NEP-141 storage_balance_of".into()),
                    });
                }
            },
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L239-254)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L330-345)
```rust
fn inner_rlp_execute(
    current_account_id: AccountId,
    predecessor_account_id: AccountId,
    target: AccountId,
    tx_bytes_b64: String,
    nonce: &mut u64,
) -> Result<Promise, Error> {
    if *nonce == u64::MAX {
        return Err(Error::AccountNonceExhausted);
    }
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-432)
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
