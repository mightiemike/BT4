### Title
Relayer fee for emulated EVM transfers is paid out via an unconditional, non-atomic promise before the underlying action's success is known - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
`WalletContract::rlp_execute` (via `inner_rlp_execute`) creates a separate `refund_promise` that unconditionally transfers the relayer `fee` to the predecessor account as soon as the transaction is parsed, before the promise that actually performs the underlying `EOABaseTokenTransfer`/`ERC20Transfer` action is created or resolved.

### Finding Description
When `inner_rlp_execute` parses a `TransactionKind::EthEmulation(EthEmulationKind::EOABaseTokenTransfer { fee, .. })` or `ERC20Transfer { fee, .. }`, it immediately (and unconditionally, so long as `fee` is non-zero and the caller is not itself) issues a `promise_batch_create` + `promise_batch_action_transfer` to pay the relayer's fee: [1](#0-0) 

This fee-payment promise is a receipt independent from the receipt(s) that carry out the actual requested action (transfer / `ft_transfer` / `storage_deposit`), which are constructed afterward in the same function via `action_to_promise(...).then(ext.rlp_execute_callback(...))` or via the `nep_141_storage_balance_callback`/`address_check_callback` chains: [2](#0-1) 

Because NEAR provides no atomicity across separate action receipts spawned from the same function call — a limitation explicitly documented for the protocol — there is no guarantee that the fee-payment receipt and the main-action receipt succeed or fail together: [3](#0-2) 

The callback that eventually resolves the main action (`rlp_execute_callback`) only refunds the `caller_deposit` (the value attached beyond what was needed for the action) when the promise fails — it never claws back or accounts for the `fee` already sent to the relayer in a separate, earlier receipt: [4](#0-3) 

This is directly analogous to the reported issue: a protocol that lacks a mechanism to atomically bundle a "pay fee" step with the operation it is meant to compensate, exposing the paying party to loss of the fee if the compensated operation later fails (e.g., insufficient gas/balance for the ERC-20 transfer, `ft_transfer` failing due to the receiver being unregistered in ways not anticipated, or `storage_deposit`/`ft_transfer` batch running out of gas) — the relayer is paid regardless of whether the user's intended action ultimately completes.

### Impact Explanation
If the underlying action fails after the fee receipt has already succeeded, the account owner has paid the relayer's compensation without receiving the requested service, and there is no refund path for that lost `fee` (only the `caller_deposit`, a different value, is refunded on failure). This is an unauthorized/unintended value transfer from the user's perspective: value leaves the account irreversibly whenever the fee receipt executes, decoupled from the correctness or success of the paid-for action. Given the wallet contract is meant to emulate Ethereum EOAs for real user funds, this can cause concrete loss of funds for users interacting through relayers, matching the "unauthorized value movement" / "permanently frozen or lost funds" acceptance criteria.

### Likelihood Explanation
This path is reachable by any ordinary user/relayer submitting an RLP-encoded Ethereum transaction with `EOABaseTokenTransfer`/`ERC20Transfer` and non-zero `fee` through `rlp_execute` — no privileged access or malicious node/validator is required. The order-of-execution risk is inherent to NEAR's receipt model (as documented), so it will manifest whenever the main action receipt fails for any reason after the fee receipt has already been scheduled/executed (e.g., gas exhaustion in the `ft_transfer`/`storage_deposit` batch, or downstream contract logic reverting).

### Recommendation
Chain the fee-payment transfer as a step within the same promise/receipt sequence that performs the main action (e.g., append the fee transfer only after the action succeeds, inside the success branch of `rlp_execute_callback`, or make the fee payment part of the same batched promise as the action) rather than issuing it as an independent, earlier receipt. This ensures the relayer is compensated only if — and atomically with — the user's requested action actually succeeds, or alternatively track the fee similarly to `CallerDeposit` so it can be refunded to the user if the main action fails.

### Proof of Concept
1. User signs an RLP `ERC20Transfer` (or `EOABaseTokenTransfer`) transaction with a non-zero relayer `fee`, targeting a token contract/receiver, and submits it via a relayer calling `rlp_execute`.
2. `inner_rlp_execute` parses the transaction, and because `fee` is non-zero and `predecessor_account_id != current_account_id`, immediately creates and dispatches the fee-transfer promise to the relayer (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:374-385`).
3. Separately, the main `ERC20Transfer` promise chain (`storage_balance_of` → possibly `storage_deposit` → `ft_transfer` → `rlp_execute_callback`) is constructed and dispatched (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:433-458`).
4. The fee-transfer receipt executes and succeeds (relayer is paid).
5. The main action receipt chain later fails (e.g., the token contract's `ft_transfer` panics due to insufficient token balance, or gas runs out in the batch).
6. `rlp_execute_callback` observes `PromiseResult::Failed` and only refunds the `caller_deposit` (if any) — the `fee` already paid to the relayer is not recovered (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:296-312`).
7. Net result: the user's account paid the relayer's fee but received no successful transfer, with no compensating refund for the fee — an irreversible, unauthorized-in-effect loss of value caused purely by receipt-ordering/non-atomicity, not by any fault of the user.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L276-316)
```rust
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();

        if n == 0 {
            // `rlp_execute_callback` is called directly in the case of an emulated self-transfer.
            return ExecuteResponse { success: true, success_value: None, error: None };
        } else if n > 1 {
            return ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(format!(
                    "Invariant violation: this callback comes after a single promise. n={n}"
                )),
            };
        }

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
            PromiseResult::Successful(value) => {
                ExecuteResponse { success: true, success_value: Some(value), error: None }
            }
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-471)
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
```

**File:** docs/architecture/how/meta-tx.md (L86-97)
```markdown
## Limitation: Single receiver

A meta transaction, like a normal transaction, can only have one receiver. It's
possible to chain additional receipts afterwards. But crucially, there is no
atomicity guarantee and no roll-back mechanism.

For normal transactions, this has been widely accepted as a fact for how Near
Protocol works. For meta transactions, there was a discussion around allowing
multiple receivers with separate lists of actions per receiver. While this could
be implemented, it would only create a false sense of atomicity. Since each
receiver would require a separate action receipt, there is no atomicity, the
same as with chains of receipts.
```
