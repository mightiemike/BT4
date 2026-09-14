### Title
External caller's attached `CallerDeposit` is not refunded on the "invalid target" error path in `address_check_callback` - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The reported Illuminate bug is a class of "value taken from a user but the corresponding credit/refund is not issued on a specific code path, while parallel/sibling code paths correctly do so." The nearcore Wallet Contract (`near-wallet-contract`) exhibits the same bug class: `CallerDeposit` (a NEAR deposit attached by a non-relayer external caller to `rlp_execute`, tracked so it can be refunded if the emulated transaction fails) is refunded in the generic failure path of `rlp_execute_callback`, but is silently dropped (never refunded) in one specific error branch of `address_check_callback`.

### Finding Description
`CallerDeposit::new` tracks an external (non-self) caller's attached deposit specifically "to refund the caller's deposit if the cross-contract call fails" [1](#0-0) .

The refund is correctly implemented in `rlp_execute_callback`'s `PromiseResult::Failed` branch: when the underlying cross-contract call fails, the `caller_deposit` is transferred back to the original caller [2](#0-1) , and this is verified by the `test_caller_refunds` test, which asserts the external caller loses less than the attached deposit when the call fails [3](#0-2) .

However, `address_check_callback` is an alternate failure path reached for `EOABaseTokenTransfer` transactions whose target is an eth-implicit account (requiring a registrar lookup to detect a stale/faulty relayer target). When the registrar lookup indicates the target address actually now corresponds to an existing named account, and the caller is *not* the wallet's own self-signer (i.e., an external caller, exactly the case `CallerDeposit` is meant to protect), the function returns an error response directly without ever forwarding to `rlp_execute_callback` and without refunding `caller_deposit`: [4](#0-3) 

Specifically, in the branch:
```
} else {
    return PromiseOrValue::Value(ExecuteResponse {
        success: false,
        success_value: None,
        error: Some("Invalid target: target is address corresponding to existing named account_id".into()),
    });
}
```
the `caller_deposit` parameter passed into this callback is discarded entirely — the deposit that was attached by the predecessor account to `rlp_execute` and is still held by the wallet contract is never sent back. Every other rejection path either (a) increments nonce and forwards to `rlp_execute_callback` which performs the refund on ultimate failure, or (b) is reached only when `signer_account_id == current_account_id` (the wallet's own key / relayer using an access key, which is not tracked by `CallerDeposit` at all since `CallerDeposit::new` only tracks non-self predecessors). Only this one specific branch combines "external, non-self caller" with "no refund, no forwarding to the refund-performing callback."

### Impact Explanation
Any external caller (an account other than the wallet contract itself, e.g. a permissionless relayer submitting `rlp_execute` on behalf of the wallet owner, paying $NEAR as an attached deposit that should be refunded on failure) permanently loses their attached deposit whenever this specific error condition is triggered — the target eth-implicit account has since become associated with a real named account in the address registrar. The funds remain stuck in the wallet contract with no path to return them, since the contract's public interface has no other mechanism to withdraw or reclaim them for that caller. This is a concrete, transaction-triggered loss of value for an unprivileged caller (the entity funding the `rlp_execute` call), directly analogous to the referenced report's "funds transferred from lender but no compensating token/refund minted."

### Likelihood Explanation
The scenario requires: (1) a relayer submits an `rlp_execute` transaction targeting an eth-implicit account for a base-token transfer with `address_check` set (this triggers whenever a relayer is uncertain if the target has since become a real named account and thus queries the registrar), (2) the signer account is not the wallet's own current account (i.e., a relayer using its own key/deposit, not one via an added access key on the wallet), and (3) the registrar returns `Some(account_id)`, meaning the address in question really is now a named account (this can legitimately happen any time after an eth-implicit target address later gets claimed/registered as a named account — a state change fully controllable/observable on-chain, not requiring any malicious behavior). This is a normal operational condition (stale caches, relayers acting on outdated target information, or benign relayer error) rather than an edge case requiring adversarial coordination, making it realistically triggerable in production.

### Recommendation
In `address_check_callback`, in the branch handling `maybe_account_id.is_some()` combined with `env::signer_account_id() != current_account_id`, refund `caller_deposit` (if present) before returning the error `ExecuteResponse`, mirroring the refund logic already present in `rlp_execute_callback`'s `PromiseResult::Failed` arm. Alternatively, route this failure case through a shared helper that always performs the deposit-refund check prior to returning any final `ExecuteResponse::success = false` value, so future new failure branches cannot omit it.

### Proof of Concept
Conceptual trace (cannot be executed here, but derivable from code and existing tests):
1. Attacker/relayer scenario setup: wallet contract deployed at an eth-implicit account; an address is later registered to a real named account via the address registrar (as exercised in `test_register_without_deposit` at `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs:249-296`).
2. An external caller (not using the wallet's own access key) calls `rlp_execute` with a base-token-transfer Ethereum transaction whose `to` address requires an `address_check` (target is an eth-implicit account) and attaches a NEAR deposit, i.e. `caller_deposit` is populated per `CallerDeposit::new` at `types.rs:180-191`.
3. This produces a promise chain ending in `address_check_callback`. The registrar lookup returns `Some(account_id)` (the eth-implicit target is now a named account).
4. Since `env::signer_account_id() != current_account_id`, the code takes the branch at `lib.rs:168-172` returning the "Invalid target" error directly — `caller_deposit` is dropped, unlike the parallel `rlp_execute_callback::PromiseResult::Failed` refund path at `lib.rs:296-312`.
5. Result: the caller's attached deposit remains permanently in the wallet contract's balance with no compensating transfer back, verifiable by comparing the caller's balance before/after the call (analogous to the balance assertions already used in `test_caller_refunds`, `sanity.rs:170-229`), which would show the deposit was retained by the wallet contract instead of refunded.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L160-192)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L197-213)
```rust
    // External caller gets a refund when the cross-contract call fails
    let pre_tx_account_balance = caller.view_account().await?.balance;
    let receiver_id: AccountId = "fake.near".parse()?;
    let result = wallet_contract
        .rlp_execute_from(
            &caller,
            receiver_id.as_str(),
            &create_tx(&receiver_id, 0),
            deposit_amount,
        )
        .await?;
    assert!(!result.success);
    let post_tx_account_balance = caller.view_account().await?.balance;
    assert!(
        pre_tx_account_balance.as_yoctonear() - post_tx_account_balance.as_yoctonear()
            < deposit_amount.as_yoctonear()
    );
```
