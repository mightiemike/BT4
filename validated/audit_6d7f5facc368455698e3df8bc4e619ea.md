Based on my investigation, I found a valid structural analog in the `near-wallet-contract`.

### Title
Hardcoded 2 Tgas assumption for non-function-call actions in `Action::gas()` can become insufficient as base action costs change, causing loss of relayed cross-contract call value - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/near_action.rs])

### Summary
The Lido report flags a hardcoded `2300` gas stipend for ETH transfers that assumes a fixed gas cost will always be sufficient, even though EVM gas repricing (e.g. EIP-1884) can silently invalidate that assumption and break the transfer. The `near-wallet-contract`, which emulates Ethereum transaction execution for eth-implicit accounts, contains the same anti-pattern: `Action::gas()` hardcodes `Gas::from_tgas(2)` for `Transfer`, `AddKey`, and `DeleteKey` actions with the comment "2 Tgas is sufficient for any non-function call action," and this value is added into the static gas reserved for downstream callbacks.

### Finding Description
`Action::gas()` in [1](#0-0)  hardcodes 2 Tgas as sufficient budget for `Transfer`, `AddKey`, and `DeleteKey` actions regardless of the actual current protocol base action costs for these operations. This value is then used to compute the static gas attached to intermediate callbacks in `inner_rlp_execute`, e.g. `let callback_gas = ADDRESS_CHECK_CALLBACK_GAS.saturating_add(action.gas());` and `let callback_gas = NEP_141_STORAGE_BALANCE_CALLBACK_GAS.saturating_add(action.gas());` in [2](#0-1) . These callbacks (`address_check_callback`, `nep_141_storage_balance_callback`) later schedule the actual action via `action_to_promise` plus a further `.then(ext.rlp_execute_callback(...))` chained call. If the true on-chain cost of executing an `AddKey` (whose fee scales with the number/size of `method_names` and the access key data) or `DeleteKey`/`Transfer` base action ever exceeds the hardcoded 2 Tgas reservation — for instance after a runtime parameter update that increases `add_key_action_creation_config` or similar base fees, analogous to how Ethereum's SLOAD repricing under EIP-1884 broke the fixed 2300 gas stipend — the callback will run out of gas before it can schedule the follow-up `rlp_execute_callback`.

### Impact Explanation
Because the caller's attached deposit is only refunded inside `rlp_execute_callback` on `PromiseResult::Failed` (see [3](#0-2) ), a gas-exceeded failure occurring in an earlier callback (`address_check_callback` or `nep_141_storage_balance_callback`) before that refund promise is even scheduled means the `CallerDeposit` refund logic never executes. This can permanently strand the relayer/caller's attached NEAR deposit, and `has_in_flight_tx` combined with nonce bookkeeping means the account could also become stuck in an inconsistent state, satisfying the "permanently frozen funds" / transaction-triggered failure criteria reachable by any relayer submitting a meta-transaction (RLP-encoded Ethereum-style transaction) through `rlp_execute`.

### Likelihood Explanation
This requires a change in the protocol's base action costs (a runtime parameter update) to push the true cost above the hardcoded 2 Tgas margin — this is analogous to the original report's dependency on future Ethereum gas repricing. It is not exploitable today with current parameters, but the hardcoded value is a magic-number time bomb baked directly into the contract logic rather than derived from the runtime configuration, exactly mirroring the original bug's structural flaw.

### Recommendation
Do not hardcode a fixed gas amount for non-function-call actions. Instead, either use a generous, protocol-config-derived margin (queried from current runtime parameters) or use unspent-gas weighted allocation (`promise_batch_action_function_call_weight` / `GasWeight`) for the callback chain so that available margin scales automatically with the total gas budget rather than relying on a static assumption, similar to sending value via `call` (forwarding all/most remaining gas) instead of a fixed stipend as recommended in the original report.

### Proof of Concept
1. A future runtime parameter/protocol upgrade increases the base cost of `AddKey` action creation (e.g. due to increased per-`method_name` or per-byte access key fees) such that the true cost, plus the callback's own execution overhead, exceeds 2 Tgas.
2. A relayer submits an RLP-encoded Ethereum transaction via `rlp_execute` with `target` triggering the `EOABaseTokenTransfer { address_check: Some(address), .. }` path and an `AddKey` action, attaching a deposit that becomes `CallerDeposit`.
3. `inner_rlp_execute` schedules `address_check_callback` with `callback_gas = ADDRESS_CHECK_CALLBACK_GAS.saturating_add(action.gas())`, using the stale hardcoded 2 Tgas from `Action::gas()`.
4. Inside `address_check_callback`, after the registrar lookup, the code calls `action_to_promise(target, action)` and chains `.then(ext.rlp_execute_callback(caller_deposit))`; if the remaining gas is insufficient to cover the real `AddKey` base cost, the callback host function itself fails with `GasExceeded` before scheduling `rlp_execute_callback`.
5. `rlp_execute_callback`'s refund branch for `caller_deposit` never runs, and the caller's attached deposit remains locked in the wallet contract account with no path to reclaim it.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/near_action.rs (L20-27)
```rust
impl Action {
    pub fn gas(&self) -> Gas {
        match self {
            Self::FunctionCall(fn_call) => fn_call.gas,
            // 2 Tgas is sufficient for any non-function call action
            Self::Transfer(_) | Self::AddKey(_) | Self::DeleteKey(_) => Gas::from_tgas(2),
        }
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
