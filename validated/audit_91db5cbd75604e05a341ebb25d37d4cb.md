## Analog Found

### Title
Attached deposit permanently lost when the Address Registrar cross-contract call fails or the relayer-ban path is taken in the Wallet Contract - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The Wallet Contract's `address_check_callback` receives a `caller_deposit: Option<CallerDeposit>` that is supposed to guarantee refund of an external caller's attached deposit "if the cross-contract call fails," as documented on `CallerDeposit` itself. [1](#0-0)  However, in the two failure branches of `address_check_callback` — when the registrar lookup promise fails, and when the address is found to already correspond to a registered named account — the `caller_deposit` parameter is silently dropped and never refunded, unlike the analogous refund logic that exists in `rlp_execute_callback`. [2](#0-1) 

### Finding Description
`inner_rlp_execute` reaches this code path whenever an EOA base-token transfer targets another eth-implicit account, in which case the target address must first be checked against the `ADDRESS_REGISTRAR_ACCOUNT_ID` contract to ensure it is not already a named account: [3](#0-2) 

The `address_registrar` account id is a hardcoded value baked into the compiled contract at build time via `include_str!`: [4](#0-3) [5](#0-4) 

If that cross-contract call to the registrar fails for any reason (misconfigured/incorrect registrar account id, registrar contract absent/broken, insufficient gas, etc. — exactly the class of bug identified in the external report where a hardcoded/misconfigured address makes a dependent call revert), `address_check_callback` handles `PromiseResult::Failed` by returning an error response without ever using the `caller_deposit` it was given: [6](#0-5) 

The same drop of `caller_deposit` happens on the "target already registered" branch that leads into `create_ban_relayer_promise`: [7](#0-6) 

By contrast, `rlp_execute_callback` — the callback used on every other execution path — explicitly refunds `caller_deposit` on promise failure: [8](#0-7) 

`CallerDeposit` is only constructed (non-`None`) when an external, non-self predecessor attaches a non-zero deposit to the `rlp_execute` call: [9](#0-8) 

Since the deposit is attached to the top-level `rlp_execute` call (a normal NEAR `FunctionCall` action with a deposit), once that promise resolves and the contract returns without issuing a `promise_batch_action_transfer` refund, the deposited yoctoNEAR remains on the Wallet Contract's account balance with no code path left to return it to the caller (`predecessor_account_id`) — it is permanently frozen from the caller's perspective.

### Impact Explanation
This is a transaction-triggered, unprivileged fund-loss bug: any account that calls `rlp_execute` on a Wallet Contract (eth-implicit account) with a non-zero attached deposit, when the resulting transaction is an `EOABaseTokenTransfer` targeting another eth-implicit account, risks permanent loss of that deposit whenever the registrar lookup promise fails or the target turns out to be a registered account. Because the registrar account id is a fixed, hardcoded value with no update mechanism inside the running contract instance, any transient or permanent unavailability of that specific external contract (the same failure mode described in the source report) directly translates into unrecoverable loss of NEAR for callers, not merely a reverted/no-op call. This matches the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
This is reachable by any ordinary relayer or user submitting a normal signed transaction through the publicly callable `rlp_execute` method — no validator, node, or network-level compromise is required. Registrar-call failures are plausible in practice (temporary unavailability, gas mis-estimation, or a wrong/stale registrar account id), and the "target already registered" branch is a *legitimate, expected* outcome of using the wallet as designed (a user or relayer attempting to pay for account creation before the address-check completes), making the loss-of-deposit condition realistically triggerable rather than a purely theoretical edge case.

### Recommendation
In `address_check_callback`, refund `caller_deposit` in both failure branches (registrar-call failure and target-already-registered/ban-relayer branch), mirroring the refund pattern already implemented in `rlp_execute_callback`.

### Proof of Concept
1. A relayer calls `rlp_execute` on a deployed Wallet Contract (eth-implicit account) as `predecessor_account_id`, attaching a non-zero deposit, with a signed Ethereum transaction that is a native-token transfer whose `to` is another eth-implicit account address.
2. `inner_rlp_execute` classifies this as `EthEmulationKind::EOABaseTokenTransfer { address_check: Some(address), .. }` and schedules a call to `ADDRESS_REGISTRAR_ACCOUNT_ID::lookup`, chained into `address_check_callback` with `caller_deposit` set to the relayer's attached deposit. [3](#0-2) 
3. The registrar promise fails (e.g., insufficient forwarded gas, or the registrar contract does not exist/panics for that address).
4. `address_check_callback` hits `PromiseResult::Failed`, returns `ExecuteResponse { success: false, ... }`, and the relayer's attached deposit is never transferred back anywhere. [10](#0-9) 
5. The deposit remains locked in the Wallet Contract's balance, unrecoverable by the relayer who attached it.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L26-27)
```rust
const MICRO_NEAR: u128 = 10_u128.pow(18);
const ADDRESS_REGISTRAR_ACCOUNT_ID: &str = std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L133-192)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/utils/test_context.rs (L175-188)
```rust
    async fn deploy_address_registrar(worker: &Worker<Sandbox>) -> anyhow::Result<Contract> {
        let base_dir = Path::new(BASE_DIR).parent().unwrap().join("address-registrar");
        let contract_bytes = build_contract(base_dir, "eth-address-registrar").await?;
        let contract = worker.dev_deploy(&contract_bytes).await?;

        // Initialize the contract
        contract.call("new").transact().await.unwrap().into_result().unwrap();

        // Update the file where the Wallet Contract gets the address registrar account id from
        tokio::fs::write(address_registrar_account_id_path(BASE_DIR), contract.id().as_bytes())
            .await?;

        Ok(contract)
    }
```
