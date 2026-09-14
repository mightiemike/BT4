### Title
Attached NEAR deposit permanently stuck in the Wallet Contract when `rlp_execute` fails before a refund promise is created - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` is a `#[payable]` entry point that can be called by any external account with an attached NEAR deposit. This deposit is meant to be tracked via `CallerDeposit` and refunded to the caller if the requested action later fails. However, several failure branches inside `inner_rlp_execute` and `address_check_callback` return an error/response directly (`PromiseOrValue::Value(...)`) without ever creating the refund promise, causing the caller's attached deposit to be permanently absorbed into the Wallet Contract's own balance instead of being returned.

### Finding Description
`rlp_execute` accepts an attached deposit (`#[payable]`) and immediately calls `inner_rlp_execute`, which constructs a `CallerDeposit` to remember the caller and the deposit amount so it can later be refunded: [1](#0-0) 

The design intent of `CallerDeposit` is explicit — it exists solely "to refund the caller's deposit if the cross-contract call fails": [2](#0-1) 

The refund is only actually issued in one place, `rlp_execute_callback`, on a failed downstream cross-contract promise: [3](#0-2) 

However, `inner_rlp_execute` can fail for many reasons *before* any promise (and therefore before `rlp_execute_callback` can ever run) is created — e.g. invalid base64, malformed RLP, wrong chain ID, wrong nonce, `ExcessYoctoNear`, `ValueTooLarge`, unsupported actions, etc. In all of these cases the already-computed `caller_deposit` is simply dropped, and the caller (`rlp_execute`) propagates the error straight to the caller without ever spawning a refund transfer: [4](#0-3) [5](#0-4) 

The same drop-without-refund pattern also exists in `address_check_callback`, in both the "registrar call failed" and "target is an existing named account" branches, even though `caller_deposit` is passed into that function specifically to be forwarded/used for a refund: [6](#0-5) 

Because NEAR credits an `attached_deposit` to the receiving contract's account balance as soon as the receipt begins executing (before contract logic even runs), the deposit is already part of the Wallet Contract's balance the moment `rlp_execute` starts. Any code path that returns without explicitly creating a `Transfer` promise back to `caller_deposit.account_id` therefore leaves that value stuck in the Wallet Contract account forever — functionally identical to the reported Aura/Omnipool bug class where a refund is sent to `msg.sender` (the contract) instead of the actual depositor.

The `error.rs` comments confirm this is a real, externally reachable path: an "external caller" (not just a relayer with an access key) can directly trigger `RelayerError`/`UserError`/`AccountIdError` variants by calling `rlp_execute` with bad arguments — the code explicitly reasons about them paying for their own mistaken gas, but never accounts for a mistakenly/erroneously attached deposit being lost: [7](#0-6) 

### Impact Explanation
Any account (not necessarily one holding an access key to the wallet) can call `rlp_execute` directly with a NEAR deposit attached, expecting a refund if the embedded Ethereum-style transaction is rejected. If the transaction is rejected for any of the numerous validation reasons that occur prior to promise creation (bad base64/RLP, wrong nonce/chain id, oversized value, unsupported action, registrar lookup failure, or target resolving to an existing named account), the caller's deposit is permanently and irrecoverably absorbed into the Wallet Contract account instead of refunded, resulting in a direct, unauthorized loss of user funds (permanently frozen/stuck value) with no code path to recover it.

### Likelihood Explanation
This is trivially reachable by any unprivileged account: submit a single `FunctionCall` transaction to `rlp_execute` with a non-zero attached deposit and any RLP payload that fails one of the pre-promise validations (e.g., wrong nonce, invalid base64, or a target address already registered in the address registrar). No special privileges, relayer access keys, or malicious validator/node behavior are required — a normal user or a buggy front-end/relayer that attaches a deposit while making a mistake in constructing the signed Ethereum transaction will trigger this loss.

### Recommendation
In every failure branch of `inner_rlp_execute` (and the analogous branches of `address_check_callback`/`nep_141_storage_balance_callback`) that returns before a downstream promise chain is created, explicitly issue a `Transfer` promise refunding `caller_deposit.yocto_near` back to `caller_deposit.account_id`, mirroring the logic already present in `rlp_execute_callback`. Consider centralizing this into a single helper (e.g., `finalize_response(response, caller_deposit)`) that always attaches the refund transfer before returning an `ExecuteResponse`/`Error`, to avoid missing any current or future failure branch.

### Proof of Concept
1. Deploy a Wallet Contract for an eth-implicit account `0xabc...` (address `A`).
2. From any account `caller.near`, call `rlp_execute(target, tx_bytes_b64)` with `attached_deposit = 1 NEAR`, where `tx_bytes_b64` encodes a validly-signed Ethereum transaction from `A` but with an incorrect `nonce` (or invalid chain id, or `value` exceeding `VALUE_MAX`).
3. `inner_rlp_execute` reaches `validate_tx_relayer_data`, which returns `Err(Error::Relayer(RelayerError::InvalidNonce))` (or the equivalent `UserError`) — see `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs:357-359`.
4. Back in `rlp_execute`, this falls into the final `Err(e) => PromiseOrValue::Value(e.into())` arm — no promise, no refund transfer is created.
5. Observe that `caller.near`'s balance decreased by 1 NEAR (plus gas), while the Wallet Contract's account balance increased by 1 NEAR, and no receipt is ever generated to return it — the 1 NEAR is permanently stuck in the Wallet Contract account.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L106-128)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L140-192)
```rust
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
        } else {
            // We must increment the nonce at this point to prevent replay of the transaction.
            // Recall that the nonce was not incremented in `inner_rlp_execute` in the case that
            // the registrar contract was called (i.e. in the case we end up inside this callback).
            self.nonce = self.nonce.saturating_add(1);
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-345)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L389-410)
```rust
        Err(err @ Error::User(_)) => {
            // Increment nonce on all user errors to prevent replay.
            *nonce = nonce.saturating_add(1);
            return Err(err);
        }
        Err(err) => {
            // Do not increment nonce on Relayer or AccountId errors.
            // The latter error is an issue in the deployment (so the nonce is meaningless).
            // The former arises from the relayer itself doing something wrong and thus the
            // user's transaction could still be valid and potentially submitted properly by
            // another relayer. To allow this we do not increment the nonce.
            //
            // Note: if a relayer is using an access key for this wallet then that key will
            // still be revoked (in the main logic of `rlp_execute`). This fact together with
            // the condition that there only be one in-flight transaction at a time implies
            // that a relayer cannot maliciously burn a large portion of the user's tokens.
            // If the relayer is not using an access key then they are spending their own
            // resources on the gas and therefore we do not care if the relayer submits
            // the same faulty transaction multiple times.
            return Err(err);
        }
    };
```

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/error.rs (L21-26)
```rust
/// Errors which should never happen if the relayer is honest.
/// If these errors happen then we should ban the relayer (revoke their access key).
/// An external caller (as opposed to a relayer with a Function Call access key) may
/// also trigger these errors by passing bad arguments, but this is not an issue
/// (there is no ban list for external callers) because they are paying the gas fees
/// for their own mistakes.
```
