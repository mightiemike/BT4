### Title
Caller deposit is not refunded when the intermediate promise fails in `WalletContract::address_check_callback` and `WalletContract::nep_141_storage_balance_callback` - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The `near-wallet-contract` (the eth-implicit account "Wallet Contract" that emulates Ethereum transactions on NEAR) attaches a caller's deposit to the wallet's balance and tracks it in a `CallerDeposit` struct so it can be refunded if the downstream cross-contract call it triggers fails. This refund is correctly implemented in the final callback, `rlp_execute_callback`, but is missing in the two earlier callbacks in the same promise chain, `address_check_callback` and `nep_141_storage_balance_callback`. When the intermediate promise they depend on fails (or returns unparsable data), the caller's deposit is silently dropped instead of refunded, permanently locking those funds in the wallet contract's own balance.

### Finding Description
`CallerDeposit` is explicitly documented as existing to let the contract "refund the caller's deposit if the cross-contract call fails": [1](#0-0) 

The refund is correctly performed in `rlp_execute_callback` on `PromiseResult::Failed`: [2](#0-1) 

However, two earlier callbacks in the same promise chain, both of which also carry the `caller_deposit` parameter, do not perform this refund on failure:

- `address_check_callback`, triggered after looking up an address in the registrar contract for `EOABaseTokenTransfer` with `address_check`, drops `caller_deposit` on `PromiseResult::Failed` and on JSON deserialization error, returning an error `ExecuteResponse` without refunding: [3](#0-2) 

- `nep_141_storage_balance_callback`, triggered after querying `storage_balance_of` for an emulated `ERC20Transfer`, does exactly the same thing—no refund on `PromiseResult::Failed` or deserialization error: [4](#0-3) 

Both callbacks receive `caller_deposit: Option<CallerDeposit>` as a parameter (indicating the deposit is meant to be handled here too) but only forward it into the subsequent promise chain in the success path; the failure paths simply discard it: [5](#0-4) [6](#0-5) 

`CallerDeposit` is only created for a "non-self" `predecessor_account_id` (i.e., a relayer or other unprivileged account attaching a deposit when submitting the transaction to the wallet contract), and the underlying NEAR tokens are transferred to the wallet contract's balance as part of `#[payable] rlp_execute` before any of this promise chain runs: [7](#0-6) [8](#0-7) 

If the address-registrar lookup call fails (e.g., the registrar is paused, out of gas, or the account does not exist) or the token's `storage_balance_of` call fails/returns unparsable data, the attached deposit is never returned to the predecessor account. It remains permanently credited to the wallet contract's own balance, inaccessible to the depositor — directly analogous to the reported "locked collateral" class of bug where a failed/paused external transfer leaves user funds stuck without a recovery path.

### Impact Explanation
Any unprivileged relayer or third-party account that submits an RLP-encoded Ethereum transaction to a wallet contract with an attached deposit (used to compensate for gas/fees per the contract's relayer-incentive design) can have that deposit permanently and unrecoverably locked whenever the intermediate registrar-lookup or NEP-141 storage-balance-check promise fails. This is a concrete, transaction-triggered loss of funds for an unprivileged caller, matching the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
This is easily triggerable without any adversarial network or validator behavior: any relayer sending an `EOABaseTokenTransfer` with `address_check` set, or any `ERC20Transfer`, while attaching a deposit, hits this code path whenever the downstream registrar/token contract call fails for any reason (paused contract, insufficient gas, non-existent account, malformed response). No privileged access is required — a single submitted transaction with an attached deposit is sufficient.

### Recommendation
In both `address_check_callback` and `nep_141_storage_balance_callback`, mirror the refund logic used in `rlp_execute_callback`: on `PromiseResult::Failed` (and on deserialization failure) issue a `promise_batch_action_transfer` back to `caller_deposit.account_id` for `caller_deposit.yocto_near` before returning the failed `ExecuteResponse`.

### Proof of Concept
1. A relayer account (not the wallet owner) calls `rlp_execute` on a wallet contract with `target` set such that `parse_rlp_tx_to_action` returns `TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer { address_check: Some(addr), .. })`, attaching a deposit `> 0`.
2. `inner_rlp_execute` builds `CallerDeposit { account_id: relayer, yocto_near: deposit }` and schedules a call to the address registrar followed by `address_check_callback(target, action, Some(caller_deposit))`.
3. Cause the registrar lookup to fail (e.g. call an account that doesn't implement `lookup`, or one that panics/is out of gas).
4. `address_check_callback` observes `PromiseResult::Failed`, returns `ExecuteResponse { success: false, ... }` and never issues any transfer back to `relayer`.
5. Query the relayer's account balance and the wallet contract's balance: the deposit remains in the wallet contract's balance and is never returned, demonstrating permanent, unrecoverable loss of the relayer's attached funds.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L172-192)
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
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-115)
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

```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L130-159)
```rust
    /// Callback after checking if an address is contained in the registrar.
    /// This check happens when the target is another eth implicit account to
    /// confirm that the relayer really did check for a named account with that address.
    #[private]
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-221)
```rust
    #[private]
    pub fn nep_141_storage_balance_callback(
        &mut self,
        token_id: AccountId,
        receiver_id: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
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
        };
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-458)
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
```
