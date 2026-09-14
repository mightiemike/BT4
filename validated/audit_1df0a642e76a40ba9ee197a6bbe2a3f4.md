## Title
Relayer fee is paid unconditionally, before (and independent of) confirming the underlying transfer/ERC-20 action actually succeeds - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
In the eth-wallet contract's `inner_rlp_execute`, when a relayed `EOABaseTokenTransfer` or `ERC20Transfer` carries a non-zero `fee`, the contract immediately dispatches a *separate, unconditional* transfer receipt paying that fee to the relayer (`context.predecessor_account_id`), before the actual base-token/ERC-20 transfer action has even been attempted, and without linking the fee payment to the outcome of that action. [1](#0-0) 

### Finding Description
`inner_rlp_execute` parses the signed Ethereum-style transaction into a NEAR `action` and a `transaction_kind`. For fee-carrying transaction kinds, it creates a *sibling* receipt via `env::promise_batch_create(&context.predecessor_account_id)` / `env::promise_batch_action_transfer` that pays `fee` to the relayer: [2](#0-1) 

This fee-payment receipt is *not* chained (`.then(...)`) to the promise that actually performs the requested transfer/ERC-20 call, which is built and dispatched separately afterward: [3](#0-2) 

Because the two receipts are independent siblings rather than a dependency chain, the fee-transfer receipt is applied and its balance change is final regardless of whether the corresponding `Transfer`/`ft_transfer` action later succeeds or fails (e.g., due to insufficient NEAR balance on the wallet contract account, a receiver-side rejection, or any other action error surfaced only in `rlp_execute_callback`'s failure branch): [4](#0-3) 

This is structurally the same root cause as the referenced Vault report: value is credited/debited on the assumption that a dependent transfer succeeded, without verifying that outcome before finalizing the balance-affecting action.

### Impact Explanation
Any submitted RLP transaction (from the wallet owner or a relayer using an access key) that specifies a non-zero `fee` will pay that fee to the relayer even when the wallet's actual requested transfer subsequently fails on-chain. This allows unauthorized value movement out of the wallet contract's NEAR balance with no corresponding service rendered — the wallet owner is charged a fee while receiving no successful execution of their intended transfer, and the failure is only reported after the fee has already been irreversibly paid out. A relayer can trigger this deterministically by submitting a transaction whose transfer amount it can predict will fail (e.g., knows the balance will be insufficient at execution time), collecting the fee for free.

### Likelihood Explanation
Reachable directly from a single relayed transaction submitted through the wallet contract's public `rlp_execute` entry point — no privileged access, validator role, or network-level manipulation required. Any relayer (the intended "meta-transaction sender" role for this contract) can construct or opportunistically pick timing for a transaction that will fail its main action while still carrying a fee.

### Recommendation
Chain the fee-payment receipt to the outcome of the main action instead of dispatching it independently — e.g., only send the fee transfer inside `rlp_execute_callback` after confirming `PromiseResult::Successful`, or make the fee promise a batched sibling action of the *same* receipt whose failure would also roll back the fee (rather than two independent receipts). This mirrors the general fix pattern of confirming the dependent operation succeeded before finalizing balance-affecting side effects.

### Proof of Concept
1. Wallet owner signs an RLP transaction for an `EOABaseTokenTransfer` (or `ERC20Transfer`) with a non-zero `fee` and a transfer `deposit`/amount.
2. A relayer submits this via `rlp_execute`. `inner_rlp_execute` immediately creates and dispatches the fee-transfer receipt to the relayer's account. [5](#0-4) 
3. Separately, the promise performing the actual transfer (built via `action_to_promise`) is dispatched and later fails at execution time (e.g., wallet's NEAR balance insufficient for the transfer amount).
4. `rlp_execute_callback` observes `PromiseResult::Failed` and reports failure/refunds only the `caller_deposit`, but the fee already paid to the relayer in step 2 is not reverted. [6](#0-5) 
5. Net effect: relayer is paid the fee even though the user's requested transfer never completed — an unauthorized value movement out of the wallet account.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-317)
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
            PromiseResult::Successful(value) => {
                ExecuteResponse { success: true, success_value: Some(value), error: None }
            }
        }
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L433-471)
```rust
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
