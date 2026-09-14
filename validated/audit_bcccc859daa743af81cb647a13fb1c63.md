### Title
Attached deposit from an external caller can be permanently stuck in the NEAR Wallet Contract when a multi-step `rlp_execute` promise chain fails before reaching the final callback - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The `WalletContract::rlp_execute` entry point lets any unprivileged NEAR account (typically a relayer) submit an RLP-encoded Ethereum transaction on behalf of the wallet owner, optionally attaching a NEAR deposit that is tracked via `CallerDeposit` so it can be refunded if the resulting cross-contract call fails [1](#0-0) . The refund is only implemented in the terminal `rlp_execute_callback` [2](#0-1) . For transactions that require an intermediate lookup (address-registrar check or NEP-141 `storage_balance_of` check), several early-return failure branches in `address_check_callback` and `nep_141_storage_balance_callback` drop the response before that refund path is ever reached, so the caller's attached deposit is silently absorbed into the wallet contract's balance with no way to reclaim it - directly analogous to the reported `Executor` issue where value forwarded into an intermediary is left stranded when the downstream step doesn't fully consume/return it.

### Finding Description
`rlp_execute` is `#[payable]` and callable by any predecessor account; it builds an optional `CallerDeposit` for any *external* (non-self) caller who attached NEAR: [3](#0-2) [4](#0-3) .

For an `EOABaseTokenTransfer` targeting another eth-implicit account, `inner_rlp_execute` first calls out to the address registrar and only forwards `caller_deposit` into the subsequent `address_check_callback`: [5](#0-4) .

Inside `address_check_callback`, two failure branches return an `ExecuteResponse` directly without ever creating a refund promise for `caller_deposit`:
- when the registrar lookup promise itself fails or its result can't be deserialized [6](#0-5) ;
- when the target resolves to an existing named account and the caller is not the wallet's own signer (faulty-relayer case) [7](#0-6) .

The same pattern appears in `nep_141_storage_balance_callback` (used for emulated ERC-20 transfers), which returns early without a refund if the `storage_balance_of` cross-contract call fails or its result fails to deserialize: [8](#0-7) .

Only the terminal `rlp_execute_callback`, reached exclusively via the "happy path" of these functions, actually performs the refund transfer on `PromiseResult::Failed`: [2](#0-1) . Any deposit attached by an external caller that hits one of the earlier failure branches is never returned — it simply remains part of the wallet contract's own NEAR balance, unclaimed and unclaimable by the depositor, exactly matching the reported bug class of value sent into an intermediary contract that the destination logic never fully claims or refunds.

### Impact Explanation
Any external account (a relayer or any third party) that calls `rlp_execute` with attached NEAR deposit for an `EOABaseTokenTransfer` to another wallet-contract address, or for an `ERC20Transfer`, risks permanent loss of that deposit whenever the intermediate registrar lookup or `storage_balance_of` call fails (e.g., registrar unavailable, or the token contract does not exist / panics / runs out of gas). This is a concrete, transaction-triggered permanent loss of funds for an unprivileged caller with no owner-privileged or validator action involved — funds are neither refunded to the caller nor delivered to any recipient, and the wallet owner gains an unintended balance increase they did not pay for.

### Likelihood Explanation
Likelihood is moderate to high in practice: any caller can trigger the failing branch deterministically by targeting a non-existent or misbehaving token contract for an `ERC20Transfer`, or by relying on transient failures/unavailability of the fixed `ADDRESS_REGISTRAR_ACCOUNT_ID` contract for `EOABaseTokenTransfer` flows. No special privileges, races, or malicious validators are required — a single crafted transaction from any account is sufficient.

### Recommendation
Ensure every early-return branch in `address_check_callback` and `nep_141_storage_balance_callback` performs the same `caller_deposit` refund logic used in `rlp_execute_callback` before returning a failure `ExecuteResponse`, e.g. by factoring the refund-on-failure logic into a shared helper invoked on every failure path of the promise chain, not only the final callback.

### Proof of Concept
1. An external NEAR account `relayer.near` (not the wallet owner) calls `wallet.rlp_execute(target, tx_bytes_b64)` with attached deposit `D`, where the RLP transaction decodes to an `ERC20Transfer` (`target` = some `token_id`).
2. `inner_rlp_execute` builds `caller_deposit = Some(CallerDeposit { account_id: relayer.near, yocto_near: D })` and schedules `storage_balance_of` on `token_id`, chained to `nep_141_storage_balance_callback` with `caller_deposit` forwarded [9](#0-8) .
3. `token_id` is an account with no deployed contract (or a contract that panics on `storage_balance_of`), so the cross-contract call fails.
4. `nep_141_storage_balance_callback` hits `PromiseResult::Failed` and returns `ExecuteResponse { success: false, ... }` immediately, never creating a refund promise for `relayer.near`'s deposit `D` [10](#0-9) .
5. `D` yoctoNEAR remains permanently on the wallet contract's account balance; `relayer.near` has no way to reclaim it.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-159)
```rust
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
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
        };
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L160-173)
```rust
        let current_account_id = env::current_account_id();
        let promise = if maybe_account_id.is_some() {
            // We intentionally do not increment the nonce in this case because the
            // error is caused by a faulty relayer, not the user. An honest relayer
            // may still be able to successfully send the user's intended transaction.
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-220)
```rust
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-312)
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
