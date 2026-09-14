### Title
Wallet Contract pays out the relayer fee before validating the cross-contract address check, letting a malicious relayer replay the same signed transaction to drain the wallet's balance - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` sends the relayer's fee refund immediately, before it knows whether the emulated transfer will actually succeed, and it deliberately withholds the nonce increment for exactly the case (`address_check: Some(_)`) that can fail after the fee has already been paid. Because `has_in_flight_tx` is reset to `false` once the failing callback returns, an untrusted relayer can resubmit the very same signed transaction over and over, collecting the fee every time while the wallet's nonce never advances and the intended action is never executed.

### Finding Description
`inner_rlp_execute` computes the relayer fee from the user's signed Ethereum transaction and unconditionally schedules a transfer of that fee out of the wallet's own balance to the predecessor (the relayer), *before* the address-check promise that will determine whether the transaction can even proceed has resolved: [1](#0-0) 

Note that the nonce increment is explicitly skipped for the `EOABaseTokenTransfer { address_check: Some(_), .. }` case ("we still do not know if the transaction has a relayer error or not, therefore we must delay incrementing the nonce"), while the fee-refund promise below it is created unconditionally whenever `fee` is non-zero, regardless of whether `address_check` is `Some` or `None`.

The promise chain then calls `address_check_callback`, which resets the in-flight flag at the very start: [2](#0-1) 

If the registrar lookup determines the target address is in fact a registered named account, the callback takes the `maybe_account_id.is_some()` branch. When the transaction was submitted by an external relayer (not using an access key, i.e. `signer_account_id() != current_account_id`), it simply returns an error value **without banning the relayer and without incrementing the nonce**: [3](#0-2) 

Because `has_in_flight_tx` was already cleared at the top of `address_check_callback` (line 140) and is never set back to `true` on this error path, `rlp_execute`'s guard against concurrent transactions does not prevent the relayer from calling `rlp_execute` again with the identical `target`/`tx_bytes_b64`: [4](#0-3) 

Since the nonce was never consumed, and `target` can be freely chosen by the relayer to be the raw eth-implicit account-id form of an address that the relayer knows is registered in the address registrar (which always satisfies `validate_tx_relayer_data`'s target check for the `EthImplicit` case), the relayer can repeat this exact call indefinitely: every call reaches `inner_rlp_execute`, pays the fee out of the wallet's balance, then always resolves to the "target is a named account" failure branch, resetting state for another round.

### Impact Explanation
An unprivileged/untrusted relayer holding a single valid, signed transaction from a wallet owner (a normal condition of this "relay for gas" design — see docs/DataStructures/Account.md's Wallet Contract description) can replay that one transaction an unbounded number of times to siphon the wallet's own NEAR balance as "fee" payments, without the nonce ever advancing and without ever performing the user's intended action. This is a concrete unauthorized value movement out of the user's ETH-implicit wallet account, entirely reachable from a single external NEAR transaction sent to `rlp_execute`, matching the "malicious owner can steal user collateral by re-invoking a state-mutating function before the completion check finalizes" pattern in the source report.

### Likelihood Explanation
The only precondition is possession of one signed Ethereum transaction with a non-zero `max_fee_per_gas * gas_limit` fee — something a relayer is expected to have on every single relay request in normal operation of this feature. No access key or special privilege on the wallet account is required to trigger this: the attack works through the plain, permissionless `rlp_execute` entry point available to any predecessor. This makes it directly and repeatedly reachable by any user assuming the relayer role.

### Recommendation
Do not send the relayer fee refund until after the address-check (and any other pre-execution validation) has been resolved successfully — move the fee-transfer promise creation into the success branch of `address_check_callback` (and, more generally, only pay out fees once the nonce has been irrevocably consumed for that attempt), or increment the nonce atomically with the fee payment so a failing address check can never be retried with the same nonce while still having been paid.

### Proof of Concept
1. User signs one Ethereum transaction (`tx_bytes_b64`) authorizing a base-token transfer with a non-zero fee, intended for a `to` address that resolves through the registrar to some named Near account.
2. A malicious relayer calls `wallet_contract.rlp_execute(target, tx_bytes_b64)` from its own account, but deliberately sets `target` to the raw ETH-implicit account-id form matching `to` (i.e. the `EthImplicit(address)` branch), which is always accepted by `validate_tx_relayer_data` (`runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs` lines 336-350).
3. `inner_rlp_execute` does not increment the nonce (lines 358-365) but immediately schedules a fee-refund promise to the relayer from the wallet's balance (lines 374-385), then calls the address registrar.
4. The registrar confirms the address is registered to a named account, so `address_check_callback`'s `maybe_account_id.is_some()` branch runs; since the relayer is external (`signer_account_id() != current_account_id`), it returns an error value, leaving the nonce unchanged and `has_in_flight_tx` reset to `false`.
5. The relayer repeats step 2 with the exact same nonce and inputs indefinitely, each time collecting the fee from the wallet's balance while never executing the user's actual intended action, draining the wallet.

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L160-173)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L358-385)
```rust
            if let TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer {
                address_check: Some(_),
                ..
            }) = &transaction_kind
            {
            } else {
                *nonce = nonce.saturating_add(1);
            }

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
