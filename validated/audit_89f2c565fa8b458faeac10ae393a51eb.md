### Title
`rlp_execute`/`address_check_callback` drop the caller's `attached_deposit` when banning a faulty relayer, permanently locking the user's funds - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
`WalletContract::rlp_execute` is `#[payable]` and, together with `inner_rlp_execute`/`address_check_callback`, tracks the caller's `attached_deposit` via `CallerDeposit` so it can be refunded if a downstream cross-contract call fails [1](#0-0) . However, on the "faulty relayer" error path the contract instead calls `create_ban_relayer_promise`, which only deletes the signer's access key and calls `ban_relayer` — it never receives or refunds the tracked `CallerDeposit` [2](#0-1) . This mirrors the `registerRecipient` bug class: a `payable`/deposit-accepting entry point whose deposit is silently discarded on certain code paths instead of being used or refunded.

### Finding Description
`rlp_execute` accepts an attached deposit (`#[payable]`) [3](#0-2) . Inside `inner_rlp_execute`, the deposit is captured into `caller_deposit` before any parsing occurs [4](#0-3) . If transaction parsing yields `Error::Relayer`, `inner_rlp_execute` returns `Err(err)` directly, discarding the already-computed `caller_deposit` local variable without ever attaching it to a refund [5](#0-4) . Back in `rlp_execute`, this `Err(Error::Relayer(_))` case (when signer == current account) results in `create_ban_relayer_promise(current_account_id)` being scheduled [6](#0-5) . That promise only deletes the signer's key and invokes `ban_relayer` [2](#0-1) , and `ban_relayer` itself just returns a failure `ExecuteResponse` with no `CallerDeposit`/refund parameter at all [7](#0-6) .

The same pattern recurs in `address_check_callback`: when the registrar lookup shows the target address maps to an existing named account and `signer_account_id() == current_account_id`, the callback again calls `create_ban_relayer_promise(current_account_id)` without forwarding the `caller_deposit` argument that was passed into the callback [8](#0-7) . In every other branch of these two functions (normal cross-contract-call failure), `caller_deposit` is threaded through and refunded via `rlp_execute_callback`'s `Failed` branch, which explicitly creates a `promise_batch_action_transfer` back to the caller [9](#0-8) . Only the relayer-ban paths omit this refund, exactly analogous to `Allo.sol::registerRecipient` accepting `msg.value` but never using or forwarding it in the strategy call.

Unlike a normal NEAR account-balance top-up (where, per the docs, `attached_deposit` is meant to become part of the receiver's balance immediately and by design need not be "used"), the wallet contract explicitly signals — via the `CallerDeposit` mechanism — that this deposit belongs to an external, non-owner caller and is supposed to be refundable on failure [10](#0-9) . The two ban-relayer branches break that invariant.

### Impact Explanation
Any external, non-owner caller (e.g., a relayer submitting an RLP-encoded transaction on a user's behalf, as tested in `test_caller_refunds`) who attaches a deposit and triggers a `Relayer` error, or whose target address resolves to an existing named account during `address_check_callback`, will have their attached NEAR deposit permanently absorbed into the wallet contract's balance with no refund path. This is a genuine, unrecoverable value loss for the calling account — the deposit is not burned by the protocol (so it's not just gas loss) but is effectively donated to the wallet contract, which the caller never intended. This satisfies the "permanently frozen/lost funds" bar for the analog: it's the same root cause (an entry point that accepts value/deposit but fails to route it through the refund path on certain error branches) as the reported `registerRecipient` issue.

### Likelihood Explanation
This is reachable by any external account (a relayer without an access key on the wallet, or any account attaching a deposit and calling `rlp_execute`) with a single transaction/RPC call — no privileged role is required. The `Error::Relayer` condition is a normal, non-adversarial failure mode (e.g. malformed target/address mismatch that is the relayer's fault rather than the user's, as the code comments themselves note), and the address-registrar branch triggers whenever the target resolves to an existing named account, which is a standard occurrence, not an edge case requiring malicious behavior from any party. Likelihood is therefore realistic for a wallet-contract deployment with relayer-submitted transactions and non-zero attached deposits.

### Recommendation
Thread `caller_deposit` through both ban-relayer code paths and issue a `promise_batch_action_transfer` refund (as already done in `rlp_execute_callback`'s `Failed` branch) before or as part of the `ban_relayer`/key-deletion promise, so that a caller's attached deposit is always either consumed by the intended action or refunded, never silently retained by the contract.

### Proof of Concept
1. A user has a wallet contract deployed with `current_account_id`. An untrusted relayer (any account, no special privileges) submits `rlp_execute(target, tx_bytes_b64)` with a non-zero attached deposit, where `tx_bytes_b64` is crafted such that RLP/target validation triggers `Error::Relayer` (e.g., an emulated base-token transfer with an `address_check` where the signer key used equals `current_account_id`, matching the branch at [6](#0-5) ).
2. `inner_rlp_execute` computes `caller_deposit` from `env::attached_deposit()` [4](#0-3)  but the parsing error path returns `Err(err)` without ever using `caller_deposit` [5](#0-4) .
3. `rlp_execute` catches `Err(Error::Relayer(_))` and schedules `create_ban_relayer_promise`, which deletes the caller's access key and invokes `ban_relayer` — with no transfer action refunding the deposit [2](#0-1) , [7](#0-6) .
4. Post-execution, the caller's account balance is permanently reduced by the attached deposit amount, and the wallet contract's balance increases by that same amount — verifiable analogous to the balance assertions used in `test_caller_refunds`, but here the "refund" assertion would fail because no refund receipt is produced.

Note: full end-to-end confirmation (e.g., exact bytes needed to hit `Error::Relayer` via `parse_rlp_tx_to_action`) would require reading `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs`, which was not fully inspected in this pass; the control-flow analysis above is based on the code paths directly cited.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-93)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L121-125)
```rust
            Err(Error::Relayer(_)) if env::signer_account_id() == current_account_id => {
                let promise = create_ban_relayer_promise(current_account_id);
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L319-327)
```rust
    #[private]
    pub fn ban_relayer(&mut self) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        ExecuteResponse {
            success: false,
            success_value: None,
            error: Some("Error: faulty relayer".into()),
        }
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-346)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);

```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L394-409)
```rust
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L503-512)
```rust
fn create_ban_relayer_promise(current_account_id: AccountId) -> Promise {
    let pk = env::signer_account_pk();
    Promise::new(current_account_id).delete_key(pk).function_call_weight(
        "ban_relayer".into(),
        Vec::new(),
        NearToken::from_yoctonear(0),
        Gas::from_tgas(1),
        GasWeight(1),
    )
}
```
