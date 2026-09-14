### Title
Wallet Contract `address_check_callback` drops the caller's attached deposit on Address Registrar failure - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
When the NEAR eth-implicit Wallet Contract emulates a base-token transfer to another eth-implicit address, it schedules a cross-contract "lookup" call to a hardcoded `ADDRESS_REGISTRAR_ACCOUNT_ID` contract to verify that address isn't a hidden named account. If that lookup promise fails for any reason (unreachable/redeployed/paused registrar, gas exhaustion, etc.), the `address_check_callback` handler returns a failure `ExecuteResponse` directly without ever refunding the caller's attached deposit, unlike the sibling `rlp_execute_callback` which explicitly refunds `caller_deposit` on failure.

### Finding Description
`inner_rlp_execute` attaches the user's deposit to the wallet contract's own balance (deposit is transferred as part of the original transaction to the Wallet Contract receiver) and, for the `EOABaseTokenTransfer { address_check: Some(address), .. }` branch, schedules: [1](#0-0) 
a promise to `AddressRegistrar::lookup` at the hardcoded account read from a build-time file, followed by `.then(ext.address_check_callback(target, action, caller_deposit))`: [2](#0-1) 

The `caller_deposit` (constructed in `CallerDeposit::new(&context)` from the attached deposit) is meant to be refunded to the original caller if the follow-up cross-contract call ultimately fails, exactly as implemented in `rlp_execute_callback`: [3](#0-2) 

However, in `address_check_callback`, when the registrar `lookup` promise itself fails (`PromiseResult::Failed`), the function simply returns a failure response and never issues any refund transfer of `caller_deposit`: [4](#0-3) 

Because the deposit was already moved into the Wallet Contract's own account balance when the original `rlp_execute` transaction was delivered (NEAR transfers value to the receiver before contract logic runs), a failure at this specific point leaves the deposit permanently inside the Wallet Contract, with `has_in_flight_tx` reset to `false` so the contract accepts further transactions normally. There is no on-chain accounting that ties this stranded balance back to the original depositor, so it is indistinguishable from the contract's normal float and can be spent by any subsequent successful transaction issued through the same Wallet Contract (e.g., by the account owner, or a colluding/careless relayer, or simply the next legitimate spend), i.e. by "any user" able to move the account's balance going forward.

This directly parallels the external report's root cause: a cross-contract callback assumes a downstream, address-pinned contract call will always succeed; if that external dependency changes behavior (is redeployed, paused, deleted, or runs out of gas) the callback path that was supposed to safeguard/refund user funds is not exercised, and funds are left recoverable by anyone with subsequent authority over the contract's balance instead of the original depositor.

### Impact Explanation
Any relayer-submitted (or self-submitted) `rlp_execute` transaction that hits the `address_check` branch — i.e., any base-token transfer whose `target` is a different eth-implicit account than the current one — can trigger this failure path. If the registrar lookup fails (registrar contract unavailable, redeployed to a different account without updating the compiled `ADDRESS_REGISTRAR_ACCOUNT_ID`, paused, insufficient gas due to `REGISTRAR_LOOKUP_GAS` being too small for state at the time, etc.), the caller's attached deposit — which can be an arbitrary NEAR amount — is stranded in the Wallet Contract instead of being returned to the depositor. This is a concrete unauthorized value movement: value belonging to one account can end up in another account's spendable balance without any log or accounting trail attributing it back.

### Likelihood Explanation
This requires an operational condition on the external, hardcoded `ADDRESS_REGISTRAR_ACCOUNT_ID` dependency (analogous to the Stargate router being redeployed) — e.g., the registrar contract being redeployed elsewhere, paused/deleted, or simply becoming momentarily unreachable/out of gas. Since the account ID is baked into the compiled Wallet Contract bytecode via `std::include_str!`, any future change to the registrar's address (or any transient failure of the registrar call) reaches every already-deployed Wallet Contract instance immediately and unconditionally, and the failure path is reachable by an ordinary unprivileged transaction signer performing a normal base-token transfer to another eth-implicit account.

### Recommendation
In `address_check_callback`, mirror the refund logic used in `rlp_execute_callback`: when `env::promise_result(0)` is `PromiseResult::Failed`, issue a transfer of `caller_deposit` back to its `account_id` before returning the failure `ExecuteResponse`, so a failed registrar lookup cannot strand user funds inside the Wallet Contract.

### Proof of Concept
1. Deploy a Wallet Contract instance whose compiled `ADDRESS_REGISTRAR_ACCOUNT_ID` points at a registrar contract account.
2. Delete or otherwise make unreachable the registrar account (simulating a "redeploy"/decommission), or attach insufficient gas so the `lookup` call fails.
3. Submit an `rlp_execute` transaction representing an Ethereum base-token transfer whose `to` is an eth-implicit address different from the current wallet address, with a non-zero attached NEAR deposit. This routes through `inner_rlp_execute` → `EOABaseTokenTransfer { address_check: Some(address), .. }` → the `address_registrar.lookup(...).then(ext.address_check_callback(...))` chain shown at [1](#0-0) .
4. The `lookup` promise fails; `address_check_callback` hits the `PromiseResult::Failed` arm at [5](#0-4)  and returns a failure response with no refund transfer.
5. Observe: the caller's attached deposit remains in the Wallet Contract's balance (`view_account` balance increased by the deposit amount), while the depositor's balance was decreased and never restored — confirming the fund-stranding condition.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L27-27)
```rust
const ADDRESS_REGISTRAR_ACCOUNT_ID: &str = std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L134-148)
```rust
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
