## Title
Wallet Contract pays out the relayer fee refund before the address-registrar validation succeeds, letting a malicious relayer drain the wallet's balance by repeatedly replaying the same signed transaction - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
This is a valid analog of the ERC-6492 "side effects not reverted" bug class. In the Solidity report, `isValidERC6492SignatureNowAllowSideEffects` performs an externally-visible, value-affecting side effect (the factory call) *before* the ultimate signature validation completes, and that side effect is never undone if validation later fails — letting an attacker repeatedly trigger the side effect while the "real" validation never has to succeed. The NEAR Wallet Contract (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`) has the same structural flaw: it fires a NEAR balance transfer ("fee refund") to the calling relayer, and simultaneously skips advancing its replay-protection nonce, *before* the asynchronous address-registrar check that determines whether the transaction target is even valid has resolved.

### Finding Description
`inner_rlp_execute` [1](#0-0)  processes the parsed `TransactionKind`. When the parsed kind is `EthEmulationKind::EOABaseTokenTransfer { address_check: Some(_), .. }`, the nonce increment is explicitly skipped ("we still do not know if the transaction has a relayer error … therefore we must delay incrementing the nonce"): [2](#0-1) 

Immediately after, in the very same match arm, the code independently checks the `fee` field of the *same* enum variant and creates a promise that transfers NEAR from the wallet's own account to the caller (`context.predecessor_account_id`) as a "relayer refund" — with no gating on whether the address-registrar check will ultimately succeed: [3](#0-2) 

The actual validation of whether the `target` is legitimate happens later, asynchronously, via `address_registrar.lookup(address).then(address_check_callback)`: [4](#0-3) 

In `address_check_callback`, if the registrar reports the address is already a squatted named account, the nonce is still never incremented, and unless the caller happens to be the wallet's own signer (a self-relaying edge case that triggers `ban_relayer`), the callback simply returns an "Invalid target" error with no other state change: [5](#0-4) 

Because (a) the nonce is not consumed on this path, and (b) `target` is a plain, unsigned method argument to `rlp_execute` (only `tx_bytes_b64` is covered by the user's Ethereum signature) as documented in `parse_rlp_tx_to_action`, an attacker/relayer can resubmit the exact same signed `tx_bytes_b64` together with a `target` chosen to trigger the `address_check: Some(_)` branch (a target account_id that hashes to the eth-address in `tx.to`) over and over. `validate_tx_relayer_data` will accept the replay every time because `tx.nonce == expected_nonce` still holds (the contract's nonce field was never advanced): [6](#0-5) 

Each such call fires a fresh fee-transfer promise from the wallet's balance to the caller, and only refuses to advance nonce/otherwise state, matching the "arbitrary side effect performed as part of an incomplete validation, not reverted on failure" pattern in the report.

### Impact Explanation
This constitutes concrete unauthorized value movement: an unprivileged, malicious relayer who has ever seen one validly-signed `tx_bytes_b64` blob (with a non-zero `fee`) from an eth-wallet-contract owner can drain the wallet's NEAR balance in repeated small increments by resubmitting `rlp_execute` with the same `tx_bytes` and an attacker-chosen `target` that always resolves to `address_check: Some(_)` against a real squatted named account. Each call is independent gas-wise (the relayer pays gas, is compensated by `fee`), the wallet's nonce never advances, and the wallet's underlying "real" transfer action is never actually executed — so this is pure fee extraction with no bound other than the wallet's balance. This satisfies the "unauthorized value movement" acceptance criterion for a Medium/High severity analog.

### Likelihood Explanation
Reachable by any account able to call the wallet contract's public `rlp_execute` method with a previously-observed signed payload and an arbitrary `target` argument — no privileged relayer role, access key, or validator/node compromise is required. The only precondition is that at least one signed Ethereum transaction with a non-zero `fee` and a `to` address that can be paired with a target resolving to `address_check: Some(_)` has been produced by the account owner (which is the intended normal flow for onboarding via relayers, per the code's own comments), making this readily triggerable.

### Recommendation
Do not create the relayer fee-refund promise for the `address_check: Some(_)` branch until the asynchronous registrar check in `address_check_callback` has confirmed the target is valid; move the fee-transfer promise creation into the success path of `address_check_callback` (the `else` branch that also increments the nonce), so that the side effect (`fee` payment) is coupled to the same validation outcome that gates nonce consumption and action execution.

### Proof of Concept
1. Wallet owner signs one Ethereum-style transaction (base64 `tx_bytes_b64`) with `to` set to some eth-address `A`, non-zero `max_fee_per_gas`/`gas_limit` (yielding non-zero `fee`), and gives it to a relayer for a legitimate base-token transfer to another wallet-contract account.
2. Malicious relayer calls `rlp_execute(target = <a real named account whose derived hash happens to equal A>, tx_bytes_b64)`. This parses to `EthEmulationKind::EOABaseTokenTransfer { address_check: Some(A), fee }`: nonce is not incremented (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:358-365`), and the fee-refund promise pays the relayer immediately (`:374-385`).
3. `address_registrar.lookup(A)` resolves to the named account, so `address_check_callback` returns `"Invalid target: target is address corresponding to existing named account_id"` without incrementing the nonce and without banning the relayer (since `signer_account_id != current_account_id` in the normal non-self-relayed case, `:160-192`).
4. Relayer repeats step 2 with the identical `tx_bytes_b64`/`target` any number of times; `validate_tx_relayer_data` still accepts it because `tx.nonce == expected_nonce` (nonce never advanced, `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs:352-359`), collecting the `fee` payment from the wallet's balance on every call.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L330-387)
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

    let parsing_result = internal::parse_rlp_tx_to_action(&tx_bytes_b64, &target, &context, *nonce);
    let (action, transaction_kind) = match parsing_result {
        Ok((action, transaction_kind)) => {
            // Increment nonce for all cases where the registrar contract is not needed
            // to prevent replay of those transactions. For transactions that go through
            // the registrar we still do not know if the transaction has a relayer error
            // or not, therefore we must delay incrementing the nonce.
            //
            // Note: relayers with access keys cannot use this delay to needlessly spend
            // the users tokens because only one transaction is allowed to be in-flight
            // at a time.
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

            (action, transaction_kind)
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
