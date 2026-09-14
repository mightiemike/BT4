### Title
Unconditional Relayer Fee Payout Before Emulated Transfer Validates/Succeeds, Enabling Fee-Draining Replay — (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The reported bug class is about ERC-20-style contracts assuming a token transfer succeeded without checking its actual return/result, letting bookkeeping proceed on a failed transfer. In `near-wallet-contract`'s Ethereum-transaction emulation, the same class of bug appears: the relayer's fee is unconditionally transferred *before* the emulated action (base-token transfer or ERC-20/NEP-141 `ft_transfer`) is confirmed to succeed, and in the `address_check` path the nonce is deliberately not advanced. This decouples "fee paid" from "action succeeded," and because the failing transaction can be resubmitted, it allows repeated fee extraction from the wallet.

### Finding Description
In `inner_rlp_execute`, once RLP parsing succeeds, the relayer fee is sent immediately and unconditionally for `EOABaseTokenTransfer` and `ERC20Transfer` emulation kinds: [1](#0-0) 

For the `EOABaseTokenTransfer { address_check: Some(_), .. }` branch, the code explicitly *skips* incrementing `nonce` (deferring it until the async `address_check_callback` resolves), but the fee-refund `Promise` is still fired synchronously in the same code path before that callback ever runs: [2](#0-1) 

The actual outcome of the transfer is only decided later, in `address_check_callback`, which can conclude the transaction is invalid (e.g., `"Invalid target: target is address corresponding to existing named account_id"`) and return a failure `ExecuteResponse` without ever performing the intended transfer: [3](#0-2) 

Because (a) the fee payment is dispatched as an independent receipt regardless of whether the subsequent action promise later fails, and (b) the nonce is not advanced in this branch, the exact same signed transaction remains valid and can be resubmitted to `rlp_execute` by any caller (the "relayer"/predecessor) any number of times across separate blocks (the `has_in_flight_tx` guard only prevents concurrent execution, not sequential replay). Each resubmission triggers another unconditional fee transfer out of the wallet contract's balance, even though the emulated transfer never completes.

This directly mirrors the ERC-20 return-value bug class: the contract treats dispatch of an action as equivalent to its success, and pays out value (the relayer fee) without verifying the underlying transfer actually happened.

### Impact Explanation
This allows unauthorized, repeated value movement out of a NEAR Wallet Contract (an eth-implicit account) to any relayer account, without the user's transfer ever executing. Since the fee is paid on every resubmission of a transaction whose address-check ultimately fails, an adversarial or buggy relayer can drain the wallet's NEAR balance in a fee-farming loop, which constitutes unauthorized value movement — a concrete, reachable value-loss condition for any wallet-contract holder using EOA base-token-transfer emulation with a non-zero fee.

### Likelihood Explanation
Reachability is straightforward: `rlp_execute` is a public, payable entry point on the Wallet Contract, callable by any account (RPC caller / relayer) as long as `has_in_flight_tx` is false. The attacker only needs one previously-submitted, validly signed transaction from the user with `address_check: Some(_)` and a non-zero fee whose target resolves to an existing named account (deterministic outcome from the registrar), and can then resubmit it repeatedly across blocks to keep collecting the fee.

### Recommendation
Do not dispatch the relayer-fee `Transfer` action until the corresponding emulated action (and, for the `address_check` path, the registrar lookup) is confirmed successful. Chain the fee-refund transfer into the same promise/callback flow as the primary action so it only executes after `PromiseResult::Successful` is observed, and ensure `nonce` is always advanced once a signed transaction has been processed (successfully or not) to prevent replay of the exact same signed payload.

### Proof of Concept
1. User signs an `EOABaseTokenTransfer` transaction with `address_check: Some(address)` and a non-zero `fee`, targeting an address that in fact resolves (via the address registrar) to an existing named account rather than another eth-implicit wallet.
2. A relayer calls `rlp_execute(target, tx_bytes_b64)`.
3. `inner_rlp_execute` (lib.rs:358-385) does not increment `nonce` for this branch but still creates and executes the fee-transfer promise to `context.predecessor_account_id` (the relayer).
4. `address_check_callback` later resolves `maybe_account_id.is_some()==true` and returns `ExecuteResponse{success:false, ...}` — the intended transfer never happens.
5. Because `nonce` was not advanced, the relayer resubmits the identical `tx_bytes_b64` to `rlp_execute` again; step 3-4 repeat, paying the fee again.
6. Repeat until the wallet contract's NEAR balance is drained by fee payouts, with zero successful transfers ever executed.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L160-174)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L358-366)
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
