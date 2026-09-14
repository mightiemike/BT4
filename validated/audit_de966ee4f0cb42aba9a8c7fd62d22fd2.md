### Title
Unconditional Relayer Fee Payout Before Action Outcome is Known Enables Wallet-Contract Balance Drain via Replay - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (`WalletContract::rlp_execute` / `inner_rlp_execute`) pays out a relayer "fee" via an unconditional, unchecked `promise_batch_action_transfer` *before* it is known whether the wrapped Ethereum-emulated action will actually succeed. For the specific case where the target requires an asynchronous registrar lookup (`EOABaseTokenTransfer { address_check: Some(_) }`), the contract's own nonce is deliberately **not** incremented until the callback resolves — and when the callback determines the transaction was mis-targeted ("Invalid target"), the code only bans the relayer if the caller used a registered wallet access key; an arbitrary external caller that invokes `rlp_execute` directly (using its own account as predecessor, no access key needed) is not banned and the nonce remains unchanged. This lets any account replay the same previously-signed transaction indefinitely, extracting the fee transfer on every call until the wallet's balance is drained.

### Finding Description
In `inner_rlp_execute`, once parsing succeeds, the fee refund is sent unconditionally to the caller whenever it is external: [1](#0-0) 

This happens *before* the actual action (`Transfer`/`FunctionCall`) or, for eth-implicit targets, the async `address_registrar.lookup` check even executes. Crucially, the nonce increment is deliberately skipped for exactly this async-check case: [2](#0-1) 

When the registrar callback later determines the target was invalid, only a caller using the wallet's own registered access key (`signer_account_id() == current_account_id`) is penalized (its key revoked via `create_ban_relayer_promise`); any other external caller (using its own NEAR account as predecessor/signer, no access key required to call the public `rlp_execute` method) simply receives a failure response with no ban and, per the code comment, no nonce increment "because the error is caused by a faulty relayer, not the user": [3](#0-2) 

Because `rlp_execute` is a public, payable method with no access-control restriction on the caller (it is designed to be invoked by arbitrary relayers, who are separately refunded any attached deposit on failure), and because the fee payment is not gated on the outcome of the wrapped action nor blocked by nonce advancement in this specific failure path, an external caller can resubmit the exact same previously valid signed RLP transaction over and over, collecting the unconditional relayer fee on every call.

### Impact Explanation
Repeated replay drains the ETH-implicit wallet account's $NEAR balance to an arbitrary caller without the user's transfer/function-call ever executing — a concrete unauthorized value movement out of a user's wallet-contract account, reachable purely by a plain NEAR transaction/RPC call to a public contract method (no validator, relayer-key, or node privilege required).

### Likelihood Explanation
The attacker only needs to observe or otherwise obtain one previously signed RLP transaction (broadcast by the legitimate owner/relayer, e.g. from a rejected or intentionally crafted transaction whose `to` address maps to a registered named account) that carries a non-zero `fee`. No signing key, special role, or protocol privilege is needed to call `rlp_execute`; any NEAR account can invoke it directly with the captured `tx_bytes_b64`, satisfying "unprivileged transaction signer / RPC caller reachable" criteria.

### Recommendation
Do not send the relayer fee transfer until the wrapped action (including any async registrar/address check) has been confirmed successful — move the fee payout into the success branch of `rlp_execute_callback`/`address_check_callback` rather than issuing it eagerly in `inner_rlp_execute`. Additionally, ensure the nonce is always advanced (or another anti-replay mechanism, e.g. a per-signature replay guard, is enforced) for every code path that has already disbursed funds, regardless of whether the caller happens to hold a registered wallet access key.

### Proof of Concept
1. A legitimate signed Ethereum-style transaction is created with `target` set to an eth-implicit account address that is later registered as a named account in the address registrar, and with a non-zero `max_fee_per_gas * gas_limit` (yielding a non-zero `tx_fee`), per `parse_rlp_tx_to_action`: [4](#0-3) .
2. Any account (attacker) calls `wallet_contract.rlp_execute(target, tx_bytes_b64)` directly using its own NEAR account as predecessor (no access key needed, as demonstrated in `test_caller_refunds`): [5](#0-4) .
3. `inner_rlp_execute` unconditionally issues the fee transfer to the attacker's account before the registrar lookup resolves: [1](#0-0) .
4. `address_check_callback` later discovers the target actually maps to a named account (`Invalid target`) and, since `signer_account_id() != current_account_id`, simply returns a failure without banning or advancing the nonce: [6](#0-5) .
5. The attacker repeats step 2 with the identical `tx_bytes_b64` indefinitely, collecting the fee transfer each time until the wallet contract's balance is exhausted.

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L43-65)
```rust
pub fn parse_rlp_tx_to_action(
    tx_bytes_b64: &str,
    target: &AccountId,
    context: &ExecutionContext,
    expected_nonce: u64,
) -> Result<(near_action::Action, TransactionKind), Error> {
    let tx_bytes = decode_b64(tx_bytes_b64)?;
    let tx_kind: EthTransactionKind = tx_bytes.as_slice().try_into()?;
    let tx: NormalizedEthTransaction = tx_kind.try_into()?;
    let target_kind = validate_tx_relayer_data(&tx, target, context, expected_nonce)?;

    // Compute the fee based on the user's Ethereum transaction.
    // This is sent as a refund to the relayer in the case of an emulated base token
    // transfer or ERC-20 transfer. The reason for this refund is that it allows a
    // user with $NEAR to use a relayer service from their wallet immediately without
    // additional on-boarding.
    let tx_fee = {
        // Limit the cost by `VALUE_MAX` since we will convert this to a $NEAR amount.
        // The call to `low_u128` is safe because `VALUE_MAX` is the largest accepted value.
        let wei_amount = tx.max_fee_per_gas.saturating_mul(tx.gas_limit).min(VALUE_MAX).low_u128();
        NearToken::from_yoctonear(wei_amount.saturating_mul(MAX_YOCTO_NEAR as u128))
    };

```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L170-207)
```rust
// An external caller gets its deposit back if the cross-contract call fails.
#[tokio::test]
async fn test_caller_refunds() -> anyhow::Result<()> {
    let TestContext { worker, wallet_contract, wallet_sk, address_registrar, .. } =
        TestContext::new().await?;

    let caller = worker.root_account()?;
    let deposit_amount = NearToken::from_near(3);
    let create_tx = |receiver_id: &AccountId, nonce: u64| {
        let method = "register";
        let args = br#"{"account_id": "birchmd.near"}"#;
        let action = Action::FunctionCall {
            receiver_id: receiver_id.to_string(),
            method_name: method.into(),
            args: args.to_vec(),
            gas: Gas::from_tgas(10).as_gas(),
            yocto_near: 0,
        };
        utils::create_signed_transaction(
            nonce,
            receiver_id,
            Wei::new_u128(deposit_amount.as_yoctonear() / (MAX_YOCTO_NEAR as u128)),
            action,
            &wallet_sk,
        )
    };

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
```
