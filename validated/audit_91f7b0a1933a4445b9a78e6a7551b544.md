### Title
Unconditional relayer fee refund in `rlp_execute` is sent as an independent receipt not contingent on the intended transfer/action succeeding - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
In the near-wallet-contract (used by ETH-implicit accounts via `rlp_execute`), the relayer's fee refund is scheduled as a separate, independent promise batch that fires unconditionally whenever the parsed transaction has a non-zero `fee`, *before* and *decoupled from* the promise that actually performs the user's requested base-token/ERC-20 transfer. This mirrors the Slingshot bug class: a value-sufficiency assumption is baked in at "check"/decode time, but the actual value movement for the primary action happens in a separate, unlinked step whose success or partial delivery is not verified against that assumption.

### Finding Description
`inner_rlp_execute` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`, lines 330-473) parses the user's signed Ethereum transaction into a Near `Action` and a `TransactionKind`. For `EOABaseTokenTransfer` or `ERC20Transfer` kinds carrying a non-zero `fee` (computed purely from `tx.max_fee_per_gas * tx.gas_limit`, see `internal::parse_rlp_tx_to_action`, lines 54-64), the code immediately creates and dispatches a **separate** promise batch to refund the relayer: [1](#0-0) 

This `refund_promise` is created via `env::promise_batch_create` / `env::promise_batch_action_transfer` and is **not chained** (`.then(...)`) to the promise that performs the actual requested action (transfer, ERC-20 transfer, function call), which is built separately later in the same function: [2](#0-1) 

Because the two promises are independent action receipts dispatched from the same call (not causally dependent), NEAR's runtime executes and settles each on its own: if the wallet account's balance is insufficient to cover both the fee and the intended transfer/deposit amount, one receipt can fail while the other succeeds. Since the relayer-refund receipt is scheduled first and does not check that the *combined* cost of fee + intended action is actually affordable, the relayer can walk away paid while the user's intended transfer to their real recipient fails (analogous to the `finalOutputAmount >= finalAmountMin` check being satisfied "before" the value-affecting step, so the actual promised value delivered to the intended recipient can fall short or fail entirely).

Storage-staking constraints (`check_storage_stake`, `runtime/runtime/src/verifier.rs:48`) are enforced per-account at receipt-application time and are decoupled from any accounting that ties the fee payment to whether the downstream action succeeds, so there is no protocol-level or contract-level invariant preventing this split outcome.

### Impact Explanation
If the wallet balance can't cover fee + the requested transfer amount (e.g., due to concurrent spend, unexpectedly high `tx_fee` computed from an inflated `gas_limit`/`max_fee_per_gas` in the signed Ethereum payload, or an account nearing its storage-staking floor), the relayer's refund transfer succeeds as its own atomic receipt while the user's intended transfer/ERC-20 transfer fails in its own receipt. The wallet's nonce is still incremented (assuming it's not the delayed address-check path), so the transaction is treated as "spent" even though the user's real intent (moving funds to the real recipient) did not happen — while the relayer was compensated regardless. This is unauthorized value movement/misallocation of funds relative to user intent, reachable by any relayer forwarding a user-signed transaction (an unprivileged, meta-transaction-sender-reachable path).

### Likelihood Explanation
Medium. It requires the wallet account's liquid NEAR balance to be tight enough that fee + transfer amount cannot both be satisfied, which is a state a relayer or attacker who controls (or colludes with) message construction/timing can engineer (e.g. sending several nearly-balance-draining requests back-to-back, or picking a `tx_fee`/`gas_limit` combination that consumes most of the remaining balance). The `has_in_flight_tx` guard prevents concurrent `rlp_execute` calls, but does not prevent a single call itself from having fee-succeeds/action-fails skew when funds are insufficient for both amounts in the same call.

### Recommendation
Tie the relayer fee refund to the success of the intended action instead of dispatching it unconditionally and independently. For example: chain the fee-refund transfer with `.then()` after (or before, using `PromiseResult` in the callback) the primary action's promise so that both are executed as dependent steps within a single logical flow, and/or explicitly verify (before scheduling anything) that the wallet's balance can cover `fee + action_value_estimate` plus its storage-staking floor, failing fast with a relayer/user error otherwise. Alternatively, only pay the relayer fee from within the `rlp_execute_callback` after confirming the primary promise's `PromiseResult::Successful`.

### Proof of Concept
1. Fund an ETH-implicit wallet contract account with a small NEAR balance, just above the amount needed for either the fee or the transfer amount individually, but below the sum of both.
2. Construct and sign (as the account owner) an Ethereum-emulated base-token-transfer transaction with `max_fee_per_gas * gas_limit` computed to consume most of the remaining balance as `fee`, and set `value` (transfer amount) to consume the rest.
3. Submit via `rlp_execute` (any relayer, including an untrusted one, since fee computation and dispatch happen unconditionally at `inner_rlp_execute`, lines 374-385).
4. Observe: the `refund_promise` to the relayer (a distinct, non-chained action receipt, `lib.rs:382-384`) succeeds and debits the wallet; the primary transfer promise (built at `lib.rs:412-473`, chained to `rlp_execute_callback`) fails due to insufficient remaining balance. The relayer is compensated even though the user's intended transfer did not complete, and the nonce has already been incremented, precluding retry of the same signed payload.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L374-385)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-473)
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
        TransactionKind::EthEmulation(EthEmulationKind::ERC20Transfer { receiver_id, .. }) => {
            // In the case of the emulated ERC-20 transfer, the receiving account
            // might not be registered with the NEP-141 contract (per the NEP-145)
            // storage standard. Therefore we must create a multi-step promise where
            // first we check if the receiver is registered and then if not call
            // `storage_deposit` in addition to `ft_transfer`.
            let token_id = target;
            let callback_gas = NEP_141_STORAGE_BALANCE_CALLBACK_GAS.saturating_add(action.gas());
            let ext: WalletContractExt =
                WalletContract::ext(current_account_id).with_static_gas(callback_gas);
            let storage_balance_args =
                format!(r#"{{"account_id": "{}"}}"#, receiver_id.as_str()).into_bytes();
            Promise::new(token_id.clone())
                .function_call(
                    "storage_balance_of".into(),
                    storage_balance_args,
                    NearToken::from_yoctonear(0),
                    NEP_141_STORAGE_BALANCE_OF_GAS,
                )
                .then(ext.nep_141_storage_balance_callback(
                    token_id,
                    receiver_id,
                    action,
                    caller_deposit,
                ))
        }
        TransactionKind::EthEmulation(EthEmulationKind::SelfBaseTokenTransfer) => {
            // Base token transfers to self are no-ops on Near, so we do not need to
            // schedule an additional call. We can simply go straight to `rlp_execute_callback`.
            let ext: WalletContractExt =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            ext.rlp_execute_callback(caller_deposit)
        }
        _ => {
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            action_to_promise(target, action)?.then(ext.rlp_execute_callback(caller_deposit))
        }
    };
    Ok(promise)
}
```
