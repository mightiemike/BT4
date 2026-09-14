Found a valid analog. The NEAR Wallet Contract (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`) implements the same "unrefunded value on cross-contract call failure" bug class as the reported `FeePayer.sol` issue, but only partially — one failure callback refunds the attached deposit, while an earlier failure callback in the same call chain does not.

### Title
Unrefunded attached deposit gets permanently stuck in Wallet Contract on address-registrar lookup failure or invalid-target rejection - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` is `#[payable]` and accepts an attached NEAR deposit from any external caller/relayer [1](#0-0) . For external (non-self) callers, this deposit is tracked in a `CallerDeposit` so it can be refunded if the resulting cross-contract call fails [2](#0-1) . The refund is correctly issued in `rlp_execute_callback` when the final promise fails [3](#0-2) . However, for `EOABaseTokenTransfer` actions with an `address_check`, execution first routes through `address_check_callback`, which has three early-return paths — registrar call failure, malformed registrar response, and "Invalid target" (target is an existing named account) — that all return `PromiseOrValue::Value(...)` directly without ever spending the tracked `caller_deposit` [4](#0-3) .

### Finding Description
`caller_deposit` is passed into `address_check_callback` as a parameter [5](#0-4) , but is only actually used later in the function, when building the continuation promise for the success path (`.then(ext.rlp_execute_callback(caller_deposit))`) [6](#0-5) . In the three early-return branches:
1. `PromiseResult::Failed` for the registrar `lookup` call [7](#0-6) 
2. Malformed/unparsable registrar response [8](#0-7) 
3. Target resolves to an existing named account and the signer is not the wallet itself ("Invalid target") [9](#0-8) 

`caller_deposit` is dropped/never consumed. Since NEAR does not automatically return an unspent attached deposit on a `#[private]` method that merely returns a value (as opposed to spawning a `Transfer` promise), any NEAR attached to the original `rlp_execute` call by an external caller (e.g., a relayer paying gas/fee for the transaction) permanently accrues to the `WalletContract` account's own balance with no code path to reclaim it. This is directly analogous to `FeePayer.sol`'s `handleMintFee()`: an external caller's value is captured by the contract's balance and never returned when a downstream call fails, because the refund path was not implemented for every failure branch — the fix pattern in the audit report (check every failure path and either revert or explicitly refund) was only partially applied here (only `rlp_execute_callback`'s failure path refunds, not `address_check_callback`'s three failure paths).

### Impact Explanation
Any external NEAR account (an unprivileged relayer or the eth-account owner acting through a relayer) that submits an RLP-encoded Ethereum base-token-transfer transaction targeting an eth-implicit account, while attaching a NEAR deposit to the `rlp_execute` function call, will have that deposit permanently locked in the Wallet Contract's balance if:
- the Address Registrar cross-contract call fails (e.g., registrar contract paused/out of gas/unavailable), or
- the target address happens to correspond to an already-registered named account (a legitimate, easily triggerable condition, not an attacker-controlled fault).

This is a permanent loss of funds for the caller with no recovery mechanism, matching the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
This is trivially reachable by any single transaction from an unprivileged external account — no privileged role, validator behavior, or malicious peer is required. The "Invalid target" condition in particular is a normal, expected outcome (not an attacker-induced fault) that any relayer could hit simply by targeting an eth-implicit address that happens to collide with a registered named account, making this a realistically likely occurrence for legitimate relayer traffic, not just a rare edge case.

### Recommendation
In all early-return branches of `address_check_callback` (registrar call failure, malformed response, and "Invalid target"), check `caller_deposit` and, if `Some`, issue the same `env::promise_batch_create` + `env::promise_batch_action_transfer` refund pattern that `rlp_execute_callback` already uses [3](#0-2)  before returning the failure `ExecuteResponse`.

### Proof of Concept
1. Deploy the Wallet Contract for an eth-implicit account `W` and an Address Registrar contract.
2. As an external relayer account `R` (≠ `W`), call `W.rlp_execute(target, tx_bytes_b64)` attaching a nonzero NEAR deposit, where `tx_bytes_b64` encodes an Ethereum-emulated base-token transfer whose `target` is an eth-implicit account not yet checked in the registrar (`address_check: Some(address)`), following the construction in `inner_rlp_execute` [10](#0-9) .
3. `CallerDeposit::new` captures `R`'s attached deposit since `predecessor_account_id (R) != current_account_id (W)` [2](#0-1) .
4. Cause the registrar `lookup` call to fail (e.g., registrar not deployed / out of gas / panics), OR arrange for `target`'s derived address to already correspond to a registered named account so the "Invalid target" branch triggers.
5. Observe: `address_check_callback` returns `ExecuteResponse{success:false,...}` without ever calling `promise_batch_action_transfer` to refund `R`. `R`'s balance decreases by the attached deposit permanently, and `W`'s account balance increases by that amount with no code path to release it back to `R`.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-93)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L134-139)
```rust
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-172)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L179-192)
```rust
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            match action_to_promise(target, action)
                .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
            {
                Ok(p) => p,
                Err(e) => {
                    return PromiseOrValue::Value(e.into());
                }
            }
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-311)
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
