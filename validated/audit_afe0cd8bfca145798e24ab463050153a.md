## Title
Attached deposit permanently locked in the Wallet Contract when the address‑registrar lookup promise fails - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`) tracks an external caller's attached deposit via `CallerDeposit` so it can be refunded if the action it pays for ultimately fails. This refund is only issued from `rlp_execute_callback` when the final downstream promise fails. The intermediate `address_check_callback`, which is scheduled for `EOABaseTokenTransfer` transactions that require an address‑registrar lookup, has a `PromiseResult::Failed` branch that returns an error response directly without ever forwarding or refunding the tracked `caller_deposit`. Any NEAR already attached to the `rlp_execute` call (and thus already merged into the wallet's account balance per NEAR's `#[payable]` semantics) is then permanently unrecoverable by the caller who sent it.

### Finding Description
`rlp_execute` is `#[payable]`, so any deposit attached by the caller is credited to the wallet contract's account balance as soon as the transaction executes [1](#0-0) . `inner_rlp_execute` builds a `CallerDeposit` from the attached deposit specifically to be able to refund it later if the requested action fails [2](#0-1) , and `CallerDeposit::new` only produces `Some` when the predecessor (external caller) differs from the wallet's own account, i.e. precisely the case where a third-party relayer/caller fronted a deposit that must eventually go back to them [3](#0-2) .

For an `EOABaseTokenTransfer` with `address_check: Some(address)`, execution is routed through the address registrar lookup and its callback, `address_check_callback`, before the real action is ever attempted [4](#0-3) . Inside `address_check_callback`, when the registrar-lookup promise itself fails (`PromiseResult::Failed`), the function immediately returns a failure `ExecuteResponse` and never touches `caller_deposit`: [5](#0-4) 

Compare this with the only place a `caller_deposit` refund is actually implemented, `rlp_execute_callback`, which refunds it via `promise_batch_action_transfer` when the *downstream* action's promise fails [6](#0-5) . The `nep_141_storage_balance_callback` intermediate step has the same gap in its own `PromiseResult::Failed` branch [7](#0-6) .

Because `caller_deposit` is dropped (never forwarded into a further promise, and never refunded) whenever the address-registrar lookup or the NEP-141 `storage_balance_of` query itself fails as a promise (as opposed to the final action failing), the yoctoNEAR the external caller attached to their `rlp_execute` call remains permanently merged into the wallet contract's account balance. There is no code path in the contract that returns this balance to the original caller; only the wallet's owner (via a signed `rlp_execute` `Transfer` action from their own Ethereum key) can move funds out of the account, and they have no way to identify or are not obligated to return this specific fronted amount to the relayer that paid it.

### Impact Explanation
Any unprivileged relayer/caller submitting an `rlp_execute` transaction on behalf of a wallet-contract user, for an `EOABaseTokenTransfer` requiring the registrar address check (or an `ERC20Transfer` requiring the NEP‑141 storage-balance check), can have their attached deposit become permanently stuck in the target wallet contract if the intermediate cross-contract call (registrar lookup / `storage_balance_of`) fails for any reason (e.g., registrar temporarily unavailable, insufficient attached gas for the lookup, or a broken/removed registrar deployment). This is a concrete, transaction-triggered loss of funds for the caller, matching the "permanently frozen funds" / unauthorized loss-of-value class from the reference vulnerability, where funds get stuck due to an incomplete/incorrect withdrawal-refund code path.

### Likelihood Explanation
This requires no privileged access — any account (a relayer paying its own NEAR as a fee, as described in `inner_rlp_execute`'s fee-relaying comments) can trigger it simply by calling `rlp_execute` with a nonzero deposit for a transaction kind that routes through `address_check_callback` or `nep_141_storage_balance_callback`, and having the intermediate promise fail (a scenario that is realistically triggerable, e.g. by attaching insufficient gas for the registrar/`storage_balance_of` call, or if the registrar contract is briefly unavailable/misconfigured).

### Recommendation
In the `PromiseResult::Failed` branches of `address_check_callback` and `nep_141_storage_balance_callback`, forward/refund the `caller_deposit` in the same way `rlp_execute_callback` does before returning the failure `ExecuteResponse`, so an external caller's attached deposit is never silently absorbed into the wallet contract's balance regardless of which promise in the multi-step flow fails.

### Proof of Concept
1. Relayer `R` (any account, not the wallet owner) calls `rlp_execute` on wallet contract `W` (an eth-implicit account) with an RLP transaction encoding an `EOABaseTokenTransfer` to an address that is not yet a known named account, attaching some deposit `d` (fee for `R`) as allowed by `inner_rlp_execute`'s fee logic [8](#0-7) .
2. Because `address_check: Some(address)`, `inner_rlp_execute` schedules a call to the address registrar followed by `address_check_callback`, capturing `caller_deposit = Some(CallerDeposit { account_id: R, yocto_near: d })` [4](#0-3) .
3. The registrar lookup promise fails (e.g. attach too little gas for it, or the registrar account has no such method deployed).
4. `address_check_callback` observes `PromiseResult::Failed` and returns immediately with an error, never issuing any transfer back to `R` [5](#0-4) .
5. `d` remains part of `W`'s account balance; `R` has no way to reclaim it through the contract's interface.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L367-385)
```rust
            // If the action is an emulated base token or ERC-20 transfer with a non-zero fee then
            // create a promise to send the refund to the relayer. This allows any relayer
            // to safely serve base token transfers from any wallet without additional
            // on-boarding because the relayer will receive some compensation for sending
            // the transaction. Users should always verify the fee before signing a base token
            // transfer. Relayers should also verify the fee before sending to make sure the
            // user's signed transaction will refund enough to cover the relayer's gas costs.
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                fee,
                ..
            })
            | TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { fee, .. }) =
                &transaction_kind
            {
                if !fee.is_zero() && context.predecessor_account_id != context.current_account_id {
                    let refund_promise = env::promise_batch_create(&context.predecessor_account_id);
                    env::promise_batch_action_transfer(refund_promise, *fee);
                }
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
