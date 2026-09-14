### Title
Relayer Fee Sent Unconditionally Before ERC-20/Base-Token Transfer Result Is Checked - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
In `inner_rlp_execute`, the NEAR wallet contract creates an independent, unchained `Transfer` receipt paying the relayer's `fee` immediately, before the actual emulated ERC-20 (`ft_transfer`) or base-token transfer action has even been dispatched — let alone before its result (success/failure) is known. This mirrors the report's bug class: a caller performs a value-relevant action based on an ERC20-style call without checking/handling its outcome, resulting in payment despite the underlying operation potentially failing.

### Finding Description
`inner_rlp_execute` builds the fee-refund promise unconditionally and independently of the actual transfer/action promise: [1](#0-0) 

This is dispatched via `env::promise_batch_create` / `env::promise_batch_action_transfer` directly — a fire-and-forget receipt that is **not** `.then()`-chained to the promise that actually performs the `ft_transfer` (ERC-20 emulation) or native transfer: [2](#0-1) 

The actual transfer/`ft_transfer` action is created and only *afterwards* checked for success in `rlp_execute_callback`, where failure triggers a refund of the caller's *attached deposit* (`CallerDeposit`) — but not of the fee already paid to the relayer: [3](#0-2) 

Because the fee-payment receipt and the transfer receipt are sibling receipts off the same block/action rather than a chained dependency, the fee is paid to the relayer regardless of whether the emulated ERC-20 `ft_transfer` call ultimately succeeds or fails (e.g., due to insufficient token balance, a paused/blacklisted token contract, or any other NEP-141 contract-level rejection). This is the direct analog of "ignoring the return value" of an ERC-20 call: the relayer's fee-worthy action (the transfer) is treated as successful without ever gating fee payment on its actual outcome.

### Impact Explanation
An attacker acting as their own relayer (the code explicitly allows `signer_account_id() == current_account_id`, i.e., self-relaying) or a malicious/careless relayer combined with a malicious token contract set via arbitrary `target` can cause the fee to be paid out even when the underlying `ft_transfer` fails or is rejected by the token contract. This is an unauthorized value movement: NEAR tokens leave the wallet-contract account as a "fee" without the corresponding ERC-20/base-token value transfer having completed, and there is no compensating refund path for the fee once dispatched. Given the fee flows to the predecessor (`context.predecessor_account_id`), and the transaction is user-signed, this can be exploited by a self-relaying attacker to drain fees from third-party wallet-contract accounts whenever they can force the paired transfer to fail deterministically (e.g., targeting a NEP-141 token that reliably reverts under certain conditions, or one they control).

### Likelihood Explanation
Moderate. Triggering requires the attacker to control the `target` (an arbitrary NEP-141 token / account, allowed because `target` is relayer/user supplied) or a scenario where the paired transfer predictably fails post-fee-dispatch (gas exhaustion on the second cross-contract hop, non-existent method, deliberately reverting token). The comments in the code ("Relayers should also verify the fee before sending") indicate the authors were aware fee payment isn't strictly gated on transfer success, but did not account for the self-relay case or adversarial `target` contracts making this exploitable rather than just a UX risk.

### Recommendation
Chain the fee-refund transfer as a `.then()` continuation of the actual transfer/`ft_transfer` promise (or of the `rlp_execute_callback` after it resolves successfully), so the fee is only paid once `PromiseResult::Successful` is confirmed for the corresponding action, consistent with how `caller_deposit` refunds are already gated on the callback result.

### Proof of Concept
1. Deploy/select a NEP-141 token contract (or use one the attacker controls) as `target`.
2. Self-relay (submit as `signer_account_id() == current_account_id`) an RLP-encoded ERC-20 `transfer` calldata with a non-zero `fee` to a `receiver_id` that is not registered, or to a token method that will predictably panic/fail after the fee promise has already been dispatched (e.g., insufficient balance, or a target contract without `storage_deposit`/`ft_transfer` implemented as expected).
3. Observe: the fee `Transfer` receipt to `predecessor_account_id` is created in `inner_rlp_execute` at line 382-384 before the `ft_transfer` chain begins; it executes unconditionally in the same block regardless of the later `ft_transfer` outcome.
4. The `ft_transfer`/storage-deposit chain fails in `nep_141_storage_balance_callback`/`rlp_execute_callback`, `caller_deposit` is refunded, but the fee already sent at step 3 is not recovered — net value has left the wallet contract with no corresponding token transfer executed.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-316)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L433-458)
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
```
