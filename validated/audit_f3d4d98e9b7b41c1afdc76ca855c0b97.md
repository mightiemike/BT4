### Title
Wallet Contract pays relayer fee before confirming the Address Registrar lookup succeeds, allowing unbounded fee replay when the registrar call fails - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The `WalletContract::rlp_execute` flow computes and dispatches the relayer fee refund in `inner_rlp_execute` immediately, before the asynchronous cross-contract call to the hardcoded `ADDRESS_REGISTRAR_ACCOUNT_ID` contract resolves, and only increments the replay-preventing `nonce` after that call succeeds. If the registrar cross-contract call fails for any reason (e.g. a misconfigured/wrong `ADDRESS_REGISTRAR_ACCOUNT_ID`, analogous to the external report's hardcoded wrong oracle address causing all calls to revert), the nonce is never advanced, so the exact same signed transaction can be resubmitted by any caller indefinitely, each time re-triggering the fee payout.

### Finding Description
`ADDRESS_REGISTRAR_ACCOUNT_ID` is a build-time constant baked into the wasm via `include_str!` [1](#0-0) , parsed at runtime with a bare panic-on-error fallback [2](#0-1) . This mirrors the external report's root cause: a hardcoded external contract reference whose target may not expose the expected interface/method, causing the call to fail every time.

In `inner_rlp_execute`, for an `EOABaseTokenTransfer` whose target requires an address check (i.e. `address_check: Some(_)`), the nonce increment is explicitly deferred until the registrar callback resolves successfully: [3](#0-2) 

However, the fee-refund promise to the caller (`predecessor_account_id`, i.e. the relayer) is dispatched unconditionally in the very next block, regardless of whether the registrar lookup will succeed: [4](#0-3) 

The registrar is then called via `address_registrar.lookup(...)`, chained to `address_check_callback`: [5](#0-4) 

In the callback, if the registrar promise result is `Failed` (which is exactly what happens if the hardcoded account is wrong, nonexistent, or doesn't expose the expected `lookup` method — analogous to `quoteSpecificPoolsWithTimePeriod` not existing on the wrong oracle address in the source report), the function returns immediately with an error value and **does not increment the nonce**: [6](#0-5) 

Nonce advancement in this path only happens in the "successful lookup, target not registered" branch: [7](#0-6) 

Because `rlp_execute` is a public method callable by anyone with no access-key restriction (only guarded by an in-flight-transaction flag that is cleared once the promise chain resolves) [8](#0-7) , and the fee amount is computed straight from the user's originally signed Ethereum transaction (`tx.max_fee_per_gas * tx.gas_limit`) [9](#0-8) , any caller can resubmit the identical previously-observed `tx_bytes_b64`/`target` pair over and over. As long as the registrar call keeps failing (guaranteed if the hardcoded account is wrong), the nonce check in `validate_tx_relayer_data` keeps passing (the nonce never moved), and each resubmission re-dispatches the fee-refund transfer to the caller before the (doomed) registrar call is even made.

### Impact Explanation
This is a concrete unauthorized value movement / fund drain reachable by any unprivileged account that can call the target wallet contract's `rlp_execute`, given a single previously-observed signed transaction (which relayers must see in order to relay it, and which could also leak via public mempool/relayer infrastructure). It repeatedly extracts the user's fee balance to the caller without the corresponding user-intended action (the base-token transfer to another wallet contract) ever completing, because the address check can never resolve while the registrar reference is broken. This directly matches the required impact category of "concrete unauthorized value movement" and can drain the wallet contract's NEAR balance to zero via repeated relayer-fee refunds tied to a single signed message whose nonce is stuck.

### Likelihood Explanation
Likelihood is contingent on the address-registrar cross-contract call reliably failing (e.g., misconfigured/wrong `ADDRESS_REGISTRAR_ACCOUNT_ID` at build/deploy time, or the registrar account being deleted/unavailable on a shard) — the same failure mode flagged as High severity in the source report for a hardcoded wrong oracle address. Once that precondition holds, exploitation requires no special privileges: any account holding (or having observed) one valid signed transaction from the victim wallet with a nonzero fee and an eth-implicit target needing the address check can call `rlp_execute` repeatedly to keep draining fees, since the nonce that would normally block replay never advances.

### Recommendation
- Do not dispatch the fee-refund transfer before the address-registrar lookup (and any other async validation) has resolved successfully; move the fee-refund dispatch into the success path of `address_check_callback`, after the nonce has been incremented.
- Alternatively, increment the nonce (or otherwise mark the transaction as consumed) unconditionally before dispatching any promise, independent of the eventual outcome of the registrar call, so a failed cross-contract call cannot be used to indefinitely replay fee payouts.
- Add a runtime sanity check (not just a parse-or-panic) that the configured `ADDRESS_REGISTRAR_ACCOUNT_ID` resolves to a live contract exposing the expected `lookup` interface before shipping a build, and add monitoring/alerting to detect repeated `Failed` results from this specific call in production.

### Proof of Concept
1. Deploy (or misconfigure) the Wallet Contract so that the compiled-in `ADDRESS_REGISTRAR_ACCOUNT_ID` points to an account that does not implement `lookup` (or does not exist) — directly analogous to `DAIEthOracle` pointing to a Uniswap pool without `quoteSpecificPoolsWithTimePeriod`.
2. User signs one Ethereum-style transaction representing an `EOABaseTokenTransfer` to another wallet-contract-style (`0x...`) account, with `max_fee_per_gas * gas_limit` set to a non-trivial fee, per `tx_fee` computation in `parse_rlp_tx_to_action` [9](#0-8) .
3. Any account (the "relayer") calls `rlp_execute(target, tx_bytes_b64)` with this signed payload.
4. `inner_rlp_execute` immediately schedules the fee-refund transfer to the caller [10](#0-9) , then calls the broken registrar and chains `address_check_callback`.
5. The registrar call fails (`PromiseResult::Failed`); `address_check_callback` returns an error without incrementing the nonce [6](#0-5) ; `has_in_flight_tx` is cleared, allowing a new call.
6. The caller repeats step 3 with the identical `tx_bytes_b64`/`target` — nonce validation still passes because the nonce never moved, so the fee refund fires again. Repeat until the wallet contract's balance is exhausted.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L26-27)
```rust
const MICRO_NEAR: u128 = 10_u128.pow(18);
const ADDRESS_REGISTRAR_ACCOUNT_ID: &str = std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID");
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-128)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
        // To ensure user actions are executed in the desired order,
        // having multiple transactions in flight at the same time is
        // not allowed.
        if self.has_in_flight_tx {
            return PromiseOrValue::Value(ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(
                    "Error: transaction already in progress, please try again later.".into(),
                ),
            });
        }
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L174-178)
```rust
        } else {
            // We must increment the nonce at this point to prevent replay of the transaction.
            // Recall that the nonce was not incremented in `inner_rlp_execute` in the case that
            // the registrar contract was called (i.e. in the case we end up inside this callback).
            self.nonce = self.nonce.saturating_add(1);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L358-365)
```rust
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                address_check: Some(_),
                ..
            }) = &transaction_kind
            {
            } else {
                *nonce = nonce.saturating_add(1);
            }
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-431)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L59-64)
```rust
    let tx_fee = {
        // Limit the cost by `VALUE_MAX` since we will convert this to a $NEAR amount.
        // The call to `low_u128` is safe because `VALUE_MAX` is the largest accepted value.
        let wei_amount = tx.max_fee_per_gas.saturating_mul(tx.gas_limit).min(VALUE_MAX).low_u128();
        NearToken::from_yoctonear(wei_amount.saturating_mul(MAX_YOCTO_NEAR as u128))
    };
```
