### Title
Unconditional relayer-fee payout before target validation allows unbounded NEAR drain via replayed Wallet Contract transaction - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract's `rlp_execute` entry point pays a relayer "fee" for emulated Ethereum base-token / ERC-20 transfers by firing an unconditional `Promise::transfer` to `predecessor_account_id` *before* the transaction's target/relayer-honesty is validated and *before* the underlying action is known to succeed [1](#0-0) . For the `EOABaseTokenTransfer { address_check: Some(_), .. }` case, the nonce is deliberately *not* incremented until the follow-up `address_check_callback` runs [2](#0-1) , and that callback contains a branch that neither bans the relayer nor advances the nonce when `signer_account_id != current_account_id` [3](#0-2) . Because the fee payment already happened unconditionally in `inner_rlp_execute` prior to this check, the same signed Ethereum transaction can be resubmitted to `rlp_execute` repeatedly, extracting the fee amount from the wallet's own NEAR balance on every call, analogous to the referenced ERC20 report where a value transfer is not gated on confirmation that the underlying operation actually succeeded/was valid.

### Finding Description
`inner_rlp_execute` parses a relayed, RLP-encoded, user-signed Ethereum transaction and — for `EOABaseTokenTransfer` or `ERC20Transfer` emulation kinds with a non-zero `fee` — immediately creates a bare `promise_batch_create`/`promise_batch_action_transfer` sending `fee` yoctoNEAR from the wallet's own balance to `context.predecessor_account_id` (i.e., whoever called `rlp_execute`), with no promise chaining to the action that is supposedly being paid for: [4](#0-3) 

For the specific sub-case `EOABaseTokenTransfer { address_check: Some(address), .. }` (used when `target` is an eth-implicit account whose address matches `tx.to`), nonce incrementing is explicitly deferred: [2](#0-1) 

The subsequent promise chain calls the address registrar and lands in `address_check_callback`, which is the only place that either bans the relayer (revoking its access key) or increments the nonce to prevent replay: [5](#0-4) 

Crucially, when `maybe_account_id.is_some()` (the target address turns out to be registered, i.e., the caller supplied the "wrong" `target` form) and `env::signer_account_id() != current_account_id` (the caller is not using an access key belonging to the wallet itself), the code takes this path:

```
} else {
    return PromiseOrValue::Value(ExecuteResponse {
        success: false,
        success_value: None,
        error: Some("Invalid target: target is address corresponding to existing named account_id".into()),
    });
}
```

This branch does **not** call `create_ban_relayer_promise` and does **not** touch `self.nonce`. Since `self.has_in_flight_tx` is reset to `false` at the top of `address_check_callback`, the wallet contract is immediately ready to accept another call. Because `self.nonce` was never advanced (deferred at line 358-365 and never incremented in this error branch), the exact same signed Ethereum transaction still satisfies `validate_tx_relayer_data`'s nonce check on the next call [6](#0-5) , and the process repeats: fee promise fires again from the wallet's own balance to `predecessor_account_id`, address-check fails again, no ban, no nonce advance.

The `target` account ID passed to `rlp_execute` is caller-supplied, not derived solely from the signed Ethereum payload, so any caller (not necessarily the legitimate relayer or the wallet owner) can choose to invoke this vulnerable `address_check: Some` path deliberately, as long as they can obtain (once) a validly Ethereum-signed base-token transfer whose `to` happens to correspond to a *registered* NEAR account (registration in the address registrar being an ordinary, low-cost, permissionless operation observed in the test suite) [7](#0-6) .

### Impact Explanation
Any account (an ordinary NEAR transaction signer / RPC caller) can drain the wallet contract's native NEAR balance in `fee`-sized increments by repeatedly calling the public, payable `rlp_execute` method with a single previously-obtained, validly-signed Ethereum transaction, as long as the attacker frames the call to hit the `address_check: Some` + "registered address" + "non-wallet signer" branch. This bypasses the nonce-based replay protection that is supposed to guarantee each signed Ethereum transaction is honored (and its fee paid) at most once, resulting in unauthorized, repeated value movement out of the wallet account to the attacker. This is a concrete unauthorized-value-movement bug reachable purely via ordinary contract calls, matching the required "unauthorized value movement" category.

### Likelihood Explanation
Likelihood is high for any deployed ETH-emulation Wallet Contract instance that has ever had a base-token-transfer transaction signed by its owner and exposed to a third party (a normal relayer flow by design) — the attacker does not need the wallet owner's private key, does not need to be the "honest" relayer, and does not need any special permission; they only need to call the already-deployed, public `rlp_execute` method with a `target` of their choosing and the intercepted/observed signed payload, and (if necessary) register the destination address in the permissionless address registrar to force the vulnerable branch.

### Recommendation
- Do not fire the relayer-fee transfer promise until the underlying action (and, where applicable, the relayer-honesty/address-registrar check) has been confirmed successful — chain the fee-payment promise after the action/callback that validates it, mirroring the "checks before effects" pattern the original ERC20 report recommends (verify success before moving value).
- In `address_check_callback`, always increment `self.nonce` (or otherwise invalidate the signed transaction) on every terminal outcome, including the `signer_account_id != current_account_id` / registered-address error branch, so a given signed Ethereum transaction can never be reprocessed after being rejected.
- Consider deriving/validating `target` deterministically from the parsed Ethereum transaction rather than trusting an arbitrary caller-supplied `target`, closing off the ability of an unrelated caller to force the vulnerable code path.

### Proof of Concept
1. Wallet owner signs (once) a valid `EOABaseTokenTransfer` Ethereum transaction with non-zero `fee`, intending it to be relayed to another eth-implicit wallet.
2. Attacker (any NEAR account, not requiring the wallet's access key) registers the recipient address in the address registrar (permissionless, small deposit) so that `maybe_account_id` resolves to `Some`.
3. Attacker repeatedly calls `wallet_contract.rlp_execute(target = eth_implicit_account, tx_bytes_b64 = same_signed_tx)` as themselves (`signer_account_id != current_account_id`).
4. Each call: `inner_rlp_execute` unconditionally creates the fee-transfer promise to the attacker [8](#0-7) ; then `address_check_callback` finds `maybe_account_id.is_some()` and, since the caller isn't using the wallet's own key, returns an error without banning the relayer or advancing `self.nonce` [9](#0-8) .
5. `self.nonce` is unchanged, so step 3 can be repeated indefinitely with the exact same signed transaction, draining `fee` yoctoNEAR from the wallet's balance on each iteration until the wallet is emptied or gas runs out.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-192)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L366-385)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L318-359)
```rust
fn validate_tx_relayer_data<'a>(
    tx: &NormalizedEthTransaction,
    target: &'a AccountId,
    context: &ExecutionContext,
    expected_nonce: u64,
) -> Result<TargetKind<'a>, Error> {
    if tx.address.raw() != context.current_address {
        return Err(Error::Relayer(RelayerError::InvalidSender));
    }

    if tx.chain_id != Some(CHAIN_ID) {
        return Err(Error::Relayer(RelayerError::InvalidChainId));
    }

    let to = tx.to.ok_or(Error::User(UserError::EvmDeployDisallowed))?.raw();

    let target_kind = parse_target(target, context.current_address);

    // valid targets satisfy `to == target` or `to == hash(target)`
    let is_valid_target = match target_kind {
        TargetKind::CurrentAccount if to == context.current_address => {
            target == &context.current_account_id
        }
        TargetKind::EthImplicit(address) if to == address => {
            target.as_str()
                == format!("0x{}{}", hex::encode(address), context.current_account_suffix())
        }
        _ => to == account_id_to_address(target),
    };

    if !is_valid_target {
        return Err(Error::Relayer(RelayerError::InvalidTarget));
    }

    let nonce = if tx.nonce <= U64_MAX {
        tx.nonce.low_u64()
    } else {
        return Err(Error::Relayer(RelayerError::InvalidNonce));
    };
    if nonce != expected_nonce {
        return Err(Error::Relayer(RelayerError::InvalidNonce));
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/relayer.rs (L188-208)
```rust
    // Deploy a NEP-141 contract and register its address.
    // Registering should prevent a lazy relayer from setting the target incorrectly.
    let token_contract = nep141::Nep141::deploy(&worker).await?;
    let register_output: Option<String> = address_registrar
        .call("register")
        .args_json(serde_json::json!({
            "account_id": token_contract.contract.id().as_str()
        }))
        .max_gas()
        .deposit(NearToken::from_millinear(1))
        .transact()
        .await?
        .json()?;
    let token_address: [u8; 20] =
        hex::decode(register_output.as_ref().unwrap().strip_prefix("0x").unwrap())?
            .try_into()
            .unwrap();
    assert_eq!(
        token_address,
        account_id_to_address(&token_contract.contract.id().as_str().parse().unwrap(),).0
    );
```
