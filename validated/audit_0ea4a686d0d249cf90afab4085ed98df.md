## Finding

### Title
Wallet Contract loses relayer's attached deposit when an intermediate cross-contract call fails during ERC-20/EOA-with-address-check emulation - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The `near-wallet-contract` (the ETH-implicit account "wallet contract" used to emulate Ethereum transactions on NEAR) tracks a `CallerDeposit` so that an external relayer who attaches NEAR to `rlp_execute` gets refunded if the emulated transaction ultimately fails. This refund logic exists only in `rlp_execute_callback`, but two other callbacks in the same multi-step promise chains — `address_check_callback` (used for `EOABaseTokenTransfer` with an address check) and `nep_141_storage_balance_callback` (used for `ERC20Transfer`) — silently drop `caller_deposit` on the `PromiseResult::Failed` branch, never issuing the refund.

### Finding Description
`CallerDeposit::new` records the relayer's attached deposit whenever the caller (`predecessor_account_id`) differs from the wallet's own account, specifically so it can be refunded on failure: [1](#0-0) 

The only place that actually performs this refund is `rlp_execute_callback`, which explicitly creates a transfer promise back to the caller when the final promise result is `Failed`: [2](#0-1) 

However, for the two multi-step Ethereum-emulation flows, `caller_deposit` is threaded through as a parameter but is **not used** when the intermediate promise fails:

- In `address_check_callback` (used for `EOABaseTokenTransfer { address_check: Some(_), .. }`), when the registrar lookup call fails, the function returns an error `ExecuteResponse` directly without ever consuming `caller_deposit`: [3](#0-2) 

- In `nep_141_storage_balance_callback` (used for the emulated `ERC20Transfer` path), when the `storage_balance_of` call fails, the function likewise returns an error `ExecuteResponse` without consuming `caller_deposit`: [4](#0-3) 

Both of these callback methods do accept `caller_deposit: Option<CallerDeposit>` as a parameter (it is chained from `inner_rlp_execute` via `.then(ext.address_check_callback(target, action, caller_deposit))` and `.then(ext.nep_141_storage_balance_callback(token_id, receiver_id, action, caller_deposit))`), but it is only forwarded onward into the *next* promise on the success path (`action_to_promise(...).then(ext.rlp_execute_callback(caller_deposit))`), and is dropped entirely on the failure path.

The bug is structurally analogous to the referenced ERC20/ERC777 report: one code path in the same contract correctly implements the "recoverable failure" semantics (refund on failure, as `rlp_execute_callback` does), while parallel code paths that were supposed to preserve the same guarantee (the intermediate steps of ERC20 emulation and address-checked base-token transfer) silently omit it, causing an outcome (loss of the caller's funds) that violates the contract's own stated invariant ("this allows us to refund the caller's deposit if the cross-contract call fails").

### Impact Explanation
Any relayer without an access key on the wallet who submits a transaction on behalf of an EOA/eth-implicit-account (attaching NEAR to cover the relayer's compensation via `fee`, tracked as `CallerDeposit`) permanently loses that attached deposit if:
- The `ERC20Transfer` emulation's `storage_balance_of` call to the token contract fails (e.g., the token contract does not implement NEP-145, is temporarily out of gas, or the account does not exist), or
- The `EOABaseTokenTransfer`-with-address-check's registrar `lookup` call fails.

In both cases the deposit is never returned to the relayer and is not consumed by any other action; it remains stuck in the wallet contract's balance, unrecoverable by the relayer who supplied it. This is a concrete case of permanently frozen/misappropriated funds for the calling relayer, reachable directly from a normal (non-privileged) `rlp_execute` transaction.

### Likelihood Explanation
This does not require an adversarial validator, network fault, or privileged access — any relayer or the wallet-owner themselves triggering an emulated ERC-20 transfer or address-checked EOA transfer whose intermediate promise fails (a routine, easily-triggered condition: nonexistent/incompatible token contract, insufficient gas budget for `storage_balance_of`, or registrar unavailability) will hit this path. Because relayers are expected to serve arbitrary wallets/tokens without prior vetting (per the code comments), encountering a token/registrar contract that fails the intermediate call is a normal occurrence, not a contrived edge case.

### Recommendation
Make `address_check_callback` and `nep_141_storage_balance_callback` refund `caller_deposit` on their `PromiseResult::Failed` branches, mirroring the exact refund logic already present in `rlp_execute_callback` (creating a `promise_batch_create`/`promise_batch_action_transfer` back to `caller_deposit.account_id`), before returning the failure `ExecuteResponse`.

### Proof of Concept
1. A relayer without an access key calls `rlp_execute` on an eth-implicit wallet contract, submitting an RLP transaction that decodes to `EthEmulationKind::ERC20Transfer { receiver_id, fee }` with `fee` non-zero, attaching a NEAR deposit that becomes `CallerDeposit` (per `CallerDeposit::new`, lines 180-191 of `types.rs`).
2. `inner_rlp_execute` builds the promise chain: `Promise::new(token_id).function_call("storage_balance_of", ...).then(ext.nep_141_storage_balance_callback(token_id, receiver_id, action, caller_deposit))` (`lib.rs` lines 433-458).
3. The target token contract's `storage_balance_of` call fails (e.g., wrong `receiver_id` schema, contract panics, or insufficient `NEP_141_STORAGE_BALANCE_OF_GAS`).
4. `nep_141_storage_balance_callback` observes `PromiseResult::Failed` and returns `ExecuteResponse { success: false, ... }` directly (`lib.rs` lines 203-210), never issuing any refund transfer to `caller_deposit.account_id`.
5. The relayer's attached NEAR remains in the wallet contract's balance permanently; unlike the equivalent failure in `rlp_execute_callback`, no refund promise is ever created.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L133-159)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-220)
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
