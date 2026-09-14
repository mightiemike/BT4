### Title
Relayer's attached deposit is silently dropped (never refunded) when `address_check_callback` rejects a target that resolves to an existing named account - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
In the NEAR Wallet Contract (`WalletContract`), an external relayer submitting an `rlp_execute` transaction with an attached deposit (`CallerDeposit`) can have that deposit permanently absorbed into the wallet-owner's account balance with no refund path, when the transaction is an `EOABaseTokenTransfer` whose `target` resolves via the address registrar to an *existing named account*. This mirrors the referenced Cooler.sol bug class: a legitimate value-return branch is missing on an error path, so the caller (analogous to the "borrower") can never retrieve the value they attached (analogous to "collateral").

### Finding Description
`rlp_execute` is `#[payable]` — any relayer calling it attaches a NEAR deposit which becomes part of the wallet contract account's balance the moment the receipt executes, regardless of what the contract logic subsequently decides. The contract tracks this via `CallerDeposit::new(context)`, which is only populated when `predecessor_account_id != current_account_id` (i.e., the caller is an external relayer, not the account owner itself): [1](#0-0) 

This `caller_deposit` is threaded through `inner_rlp_execute` → `address_check_callback` → `rlp_execute_callback`, where the only refund path is on `PromiseResult::Failed`: [2](#0-1) 

However, `address_check_callback` has an earlier branch that terminates the flow *without ever reaching* `rlp_execute_callback`, and thus without ever issuing a refund promise: [3](#0-2) 

Specifically, when the registrar lookup returns `Some(account_id)` (the `to` address corresponds to an existing named account) and `env::signer_account_id() != current_account_id` (i.e., the caller is not itself using an access key on the wallet, the normal relayer case), the function returns:
```rust
return PromiseOrValue::Value(ExecuteResponse {
    success: false,
    success_value: None,
    error: Some("Invalid target: target is address corresponding to existing named account_id".into()),
});
```
`caller_deposit` is a parameter of this function but is never consumed on this branch — it is simply dropped. No `Promise::new(caller_deposit.account_id).transfer(...)` is ever created. The attached NEAR that the relayer paid to cover the transfer/fee is now permanently part of the wallet-contract account's `amount` balance, with no code path anywhere in the contract that returns it to the relayer. Only the wallet owner (via subsequent `rlp_execute` self-actions) could ever move that balance elsewhere — the relayer who supplied it has no way to reclaim it.

The exact same pattern (a "Failed" callback branch without a refund) also appears in `nep_141_storage_balance_callback`: [4](#0-3) 
confirming this is a repeated code-path omission, not an isolated typo.

### Impact Explanation
This is a concrete, unauthorized value movement: a relayer's attached deposit becomes permanently non-refundable and effectively donated to the wallet-owner's account balance, triggerable by a single external transaction (`rlp_execute`) to an eth-implicit account's Wallet Contract. Any relayer servicing "EOA base token transfer with address_check" requests risks having its fee/value deposit trapped whenever the registrar happens to already know the target address as a named account — a state fully controllable/predictable by an adversarial or simply unlucky combination of target address and registrar state. Funds are permanently frozen from the depositor's perspective (High: concrete unauthorized value movement / permanently frozen funds, reachable directly by an unprivileged relayer submitting a transaction, no privileged role required).

### Likelihood Explanation
Reachable by any external account (relayer) that calls `rlp_execute` with a nonzero attached deposit on behalf of an eth-implicit account, where the emulated transaction is an `EOABaseTokenTransfer` requiring `address_check` (i.e., `target` is another eth-implicit account) and the address registrar already has that address registered to a named account. This is a normal, expected operational path for relayers (the address registrar check is explicitly part of the protocol design for eth-implicit-to-eth-implicit transfers), so the trigger condition is realistic and not a contrived edge case — it will occur naturally whenever a relayer misjudges or is not up-to-date on registrar state, or when a user/attacker deliberately picks a target address matching a registered name to grief relayers.

### Recommendation
In `address_check_callback`, before returning the `ExecuteResponse{success:false, ...}` on the "target resolves to an existing named account" branch, issue the same refund promise used in `rlp_execute_callback`'s `Failed` branch: if `caller_deposit` is `Some(CallerDeposit { account_id, yocto_near })`, create a `promise_batch_create`/`transfer` (or return a `PromiseOrValue::Promise` chaining the refund) back to `account_id` for `yocto_near` before finalizing the error response. Apply the identical fix to the `PromiseResult::Failed` branch of `nep_141_storage_balance_callback`, and audit all other early-return branches across `address_check_callback`, `nep_141_storage_balance_callback`, and `inner_rlp_execute` for the same missing-refund pattern.

### Proof of Concept
1. Wallet owner W (eth-implicit account) authorizes (signs) an RLP-encoded Ethereum transaction that is an `EOABaseTokenTransfer` to another eth-implicit address `A`, with a relayer fee `fee > 0`.
2. Relayer R calls `rlp_execute(target, tx_bytes_b64)` on W's `WalletContract`, attaching a deposit covering `fee` (and/or transfer value). Since `predecessor_account_id (R) != current_account_id (W)`, `CallerDeposit::new` returns `Some(CallerDeposit{account_id: R, yocto_near: deposit})`. [5](#0-4) 
3. Because `address_check: Some(address)` is set, `inner_rlp_execute` calls the address registrar and schedules `address_check_callback(target, action, caller_deposit)`. [6](#0-5) 
4. The registrar lookup returns `Some(existing_account_id)` because `A` happens to be a registered named account, and `env::signer_account_id() != current_account_id` (the normal case for a relayer using their own access key or none at all).
5. `address_check_callback` returns `ExecuteResponse{success:false,...}` at lines 168–173 without ever creating a refund promise for `caller_deposit`.
6. R's attached NEAR deposit remains part of W's `WalletContract` account balance permanently; R has no way to reclaim it through any exposed method of the contract.

Note: I was unable to locate any existing unit test exercising this specific branch/refund behavior in the indexed test files (`runtime/near-wallet-contract/implementation/wallet-contract/src/tests/`), so I cannot confirm whether this is already covered/guarded elsewhere in code not captured by the index. If the repository contains additional protective logic outside what was indexed, a Devin session with full file access should verify this before treating the PoC as final.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-221)
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
