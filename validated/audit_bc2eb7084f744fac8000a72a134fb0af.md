### Title
Relayer fee is transferred unconditionally before the underlying action executes or the address check resolves - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
This is the same "ignore failure status of a called/queued operation and treat it as if it succeeded" bug class as the CToken report, applied to the NEAR Wallet Contract's relayer‑fee refund. `inner_rlp_execute` unconditionally schedules a NEAR transfer paying the relayer's fee *before* it is known whether the address-registrar check (which decides if the relayer behaved honestly) will complete, and independently of whether the actual user action (base‑token transfer / ERC‑20 transfer) ever executes successfully.

### Finding Description
In `inner_rlp_execute` (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:374-385`), whenever the parsed transaction is an `EOABaseTokenTransfer` or `ERC20Transfer` with a non-zero `fee`, the contract immediately fires off a **separate, unlinked** promise batch that transfers the relayer fee: [1](#0-0) 

This `refund_promise` is created with `env::promise_batch_create` and is **not** chained via `.then()` to the promise that actually performs the user's requested action (`action_to_promise(...)`, returned separately at line 472 and awaited by `rlp_execute_callback`). It is fire‑and‑forget.

Crucially, for the `EOABaseTokenTransfer { address_check: Some(address), .. }` case, the contract explicitly *skips* incrementing the nonce at this point (`lines 358-365`) because it does not yet know whether the relayer supplied the wrong target (an eth-implicit address instead of the real named account) — a case that is only detected later, asynchronously, in `address_check_callback` (lines 130-192), which calls `create_ban_relayer_promise` if the relayer is found to be faulty: [2](#0-1) 

Despite the relayer's honesty being unresolved at this point, the fee transfer promise for the same transaction has *already* been dispatched in `inner_rlp_execute`, unconditionally, before the registrar lookup (`address_registrar.lookup(...)`) even runs. There is no code path that checks the `PromiseResult` of the address-check lookup, of `action_to_promise`, or of the final action, before releasing the fee — the fee payment's success/failure is never gated on the success/failure of the action it is supposed to compensate for.

This mirrors the CToken bug class exactly: an operation whose completion status carries semantic information (did the relayer behave correctly? did the requested action succeed?) is not checked, and downstream value movement (the fee payment) proceeds regardless of that status.

### Impact Explanation
A relayer (an unprivileged predecessor account calling `rlp_execute`) can submit a transaction whose `target` is deliberately set to an eth-implicit address that is *actually* registered in the address registrar (i.e., a case that will be flagged as "faulty relayer" and result in `ban_relayer`/key revocation only after the fact). Because:
1. The fee-refund promise is dispatched unconditionally in `inner_rlp_execute`, before the registrar lookup resolves, and
2. The nonce is deliberately *not* incremented for this branch (so the same signed transaction can be resubmitted),

a malicious relayer can repeatedly resubmit the same (nonce-unconsumed) transaction, collecting the fee transfer from the wallet contract's own NEAR balance on every submission attempt, even though the actual user-intended action never succeeds (the relayer is banned each time via key revocation, but a new relayer key / new caller can retry, and the fee has already left the wallet's balance on each faulty attempt). This results in unauthorized, non-consensual drainage of the wallet's NEAR balance disconnected from whether any useful action was performed — a concrete, transaction-triggered loss of user funds.

### Likelihood Explanation
Reachable by any account holding (or briefly holding, e.g. via a granted function-call access key) a relayer key for the wallet contract, or any predecessor account calling `rlp_execute` with `attached_deposit` covering the fee scenario — i.e., a single, ordinary `FunctionCall` transaction to `rlp_execute`. No validator, network, or privileged access is required; the relevant code path (`EOABaseTokenTransfer` with `address_check: Some(_)` and non-zero fee) is reachable purely through crafting the RLP-encoded Ethereum transaction payload passed to `rlp_execute`.

### Recommendation
Do not dispatch the fee-refund transfer as an independent, unlinked promise. Instead:
- Defer the fee payment until after the address-check callback (and the main action) resolves successfully, chaining it via `.then()` off of the same promise that performs the user's action, or
- Include the fee transfer as a batched action alongside the actual action in the same receipt/promise so that a single failure (e.g., "faulty relayer" detection) also prevents the fee payment, and
- Only release the fee once the ultimate outcome (`ExecuteResponse.success`) is known to be `true`, mirroring the general recommendation of validating the completion status of every dependent operation before finalizing value transfers.

### Proof of Concept
1. Relayer holds (or is granted) an access key on the target wallet contract account (or simply calls `rlp_execute` as predecessor with the required deposit).
2. Craft an RLP Ethereum transaction whose `to` address corresponds to an eth-implicit account that is *also* registered under a named account in the address registrar, with `max_fee_per_gas * gas_limit` set to a non-trivial fee, and attach enough deposit/gas.
3. Call `wallet_contract.rlp_execute(target, tx_bytes_b64)`.
4. Observe: `inner_rlp_execute` immediately issues `env::promise_batch_action_transfer(refund_promise, fee)` to the caller (lines 382-384) before the registrar lookup / `address_check_callback` resolves.
5. `address_check_callback` subsequently detects the faulty target and issues `create_ban_relayer_promise`, but the fee has already been transferred in step 4 and the nonce was never incremented (lines 358-365), permitting the same signed transaction to be resubmitted to repeat the fee extraction.

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
