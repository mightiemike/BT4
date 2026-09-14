Confirmed: `CallerDeposit` is only populated when an external (non-relayer, non-self) caller attaches a deposit directly to `rlp_execute` (e.g., an unprivileged transaction signer / RPC caller invoking `rlp_execute` with an attached deposit rather than going through a Function-Call access-key relayer). The comment at [1](#0-0)  explicitly states the purpose is "to refund the caller's deposit if the cross-contract call fails," and `rlp_execute_callback` does implement that refund on `PromiseResult::Failed` at [2](#0-1) . However, the two other callbacks that also carry `caller_deposit` through the promise chain — `address_check_callback` and `nep_141_storage_balance_callback` — drop it silently on their own `PromiseResult::Failed` branches, exactly mirroring the Illuminate `convert()` bug class (external/cross-contract call fails, error is "handled" only by returning an error value, with no compensating action for funds already committed).

### Title
Wallet Contract loses external caller's attached deposit when the address-registrar or NEP-141 storage-balance cross-contract call fails - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
`WalletContract::rlp_execute` accepts a deposit from an external (non-relayer) caller and stores it as `CallerDeposit` so it can be refunded if a subsequently scheduled cross-contract call fails [3](#0-2) . This refund is correctly performed in `rlp_execute_callback` [2](#0-1) , but two other callbacks in the same promise-chain family — `address_check_callback` and `nep_141_storage_balance_callback` — also thread `caller_deposit` through and reach their own `PromiseResult::Failed` arm, yet never issue the refund transfer there.

### Finding Description
`inner_rlp_execute` builds `caller_deposit` once via `CallerDeposit::new(&context)` [4](#0-3)  and passes it forward to whichever first-stage callback is scheduled, depending on the parsed transaction kind:
- For an `EOABaseTokenTransfer` with `address_check: Some(address)`, the flow goes through `address_registrar.lookup(...).then(address_check_callback(target, action, caller_deposit))` [5](#0-4) .
- For an `ERC20Transfer`, the flow goes through `storage_balance_of(...).then(nep_141_storage_balance_callback(token_id, receiver_id, action, caller_deposit))` [6](#0-5) .

In both of these callbacks, when the underlying cross-contract call (`lookup` on the address registrar, or `storage_balance_of` on the NEP-141 token) fails, the code returns an `ExecuteResponse{success:false, ...}` immediately without ever using the `caller_deposit` parameter:

```
PromiseResult::Failed => {
    return PromiseOrValue::Value(ExecuteResponse {
        success: false,
        success_value: None,
        error: Some("Call to Address Registrar contract failed".into()),
    });
}
``` [7](#0-6) 

```
PromiseResult::Failed => {
    return PromiseOrValue::Value(ExecuteResponse {
        success: false,
        success_value: None,
        error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
    });
}
``` [8](#0-7) 

Contrast this with the only path that does implement the refund, `rlp_execute_callback`: [2](#0-1) 

Because `has_in_flight_tx` is also reset to `false` in the failing callbacks before returning [9](#0-8) [10](#0-9) , the wallet contract's attached deposit from the external caller is simply absorbed into the wallet's own balance permanently — there is no compensating action, exactly the "external call fails, error handling incomplete, funds lost" pattern from the Illuminate report (`Converter.convert`/`Redeemer.redeem` silently continuing after a failed conversion).

This is reachable by any unprivileged account: any Near account can call `rlp_execute` on someone else's eth-implicit wallet contract, attach a NEAR deposit, and supply an RLP transaction whose `target` triggers either the address-registrar lookup path or the NEP-141 `ERC20Transfer` path. If the registrar or NEP-141 token contract is unreachable, deleted, out of gas, or otherwise fails that specific cross-contract call, the caller's deposit is not returned.

### Impact Explanation
An external caller's attached NEAR deposit (used to cover potential relayer/transfer fees per the wallet-contract's own design, see `test_caller_refunds`) can be permanently lost/absorbed by the wallet contract whenever the intermediate address-registrar lookup or NEP-141 `storage_balance_of` call fails, instead of being refunded as the contract's documented invariant promises. This is a direct unauthorized-value-transfer/loss-of-funds bug for any account attaching a deposit while calling `rlp_execute` targeting an eth-implicit account or an ERC‑20-emulated token transfer.

### Likelihood Explanation
This is triggerable by a single external transaction/contract call from any account (no privileged role needed), and only requires the address registrar or NEP-141 token contract's queried method to fail (e.g., account doesn't exist, contract paused, temporarily out of gas, or simply not deployed) — a condition fully within an attacker's control by choosing an appropriate `target`/token account, or simply relying on naturally-occurring failures. The existing test suite (`test_caller_refunds`) only exercises the refund on the final `rlp_execute_callback` path and does not cover `address_check_callback` or `nep_141_storage_balance_callback`, which is consistent with this refund gap not being caught by tests.

### Recommendation
In `address_check_callback` and `nep_141_storage_balance_callback`, replicate the refund logic used in `rlp_execute_callback` inside their `PromiseResult::Failed` arms: if `caller_deposit` is `Some(CallerDeposit { account_id, yocto_near })`, create a `promise_batch_create`/`promise_batch_action_transfer` back to `account_id` for `yocto_near` before returning the failure `ExecuteResponse`.

### Proof of Concept
1. An external (non-relayer) account `caller.near` calls `wallet_contract.rlp_execute(target, tx_bytes_b64)` with an attached deposit (e.g. 3 NEAR), where the RLP-encoded transaction is either:
   - an `EOABaseTokenTransfer` whose `to` address is not a Near-native action and requires the address-registrar check (`address_check: Some(address)`), with `target` being some other eth-implicit account, or
   - an `ERC20Transfer` whose `target` (token contract) will be queried via `storage_balance_of`.
2. `inner_rlp_execute` computes `caller_deposit = CallerDeposit::new(&context) = Some({account_id: caller.near, yocto_near: 3 NEAR})` [4](#0-3) , and schedules `address_registrar.lookup(...).then(address_check_callback(..., caller_deposit))` or the NEP‑141 equivalent.
3. Make the cross-contract call fail — e.g. supply a `target` for a nonexistent/unregistered NEP‑141 contract account, or one that runs out of gas, so `storage_balance_of`/`lookup` returns `PromiseResult::Failed`.
4. `address_check_callback`/`nep_141_storage_balance_callback` executes its `PromiseResult::Failed` branch, returning `ExecuteResponse{success:false,...}` and never scheduling any transfer back to `caller.near`.
5. Observe: `caller.near`'s balance decreased by the deposit permanently, and the wallet contract's balance increased by the same amount, with no way for the caller to reclaim it (contrast with `test_caller_refunds`, which only demonstrates the refund happens along the `rlp_execute_callback` path and not along these two).

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L140-140)
```rust
        self.has_in_flight_tx = false;
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L202-202)
```rust
        self.has_in_flight_tx = false;
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-345)
```rust
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
