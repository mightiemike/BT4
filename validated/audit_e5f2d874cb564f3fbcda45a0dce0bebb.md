### Title
Permanent Loss of NEAR Funds in `WalletContract::nep_141_storage_balance_callback` When a Batched `storage_deposit` + `ft_transfer` Call Partially Fails - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (`near-wallet-contract`), used to let ETH-implicit accounts execute RLP-encoded Ethereum transactions as NEAR actions, pays a NEP-141 `storage_deposit` out of its own account balance when emulating an ERC-20 transfer to an unregistered receiver. This deposit and the subsequent `ft_transfer` call are chained as two actions inside a single NEAR promise batch. If the batch's second action (`ft_transfer`) fails after the first (`storage_deposit`) already succeeded, the wallet's failure-handling callback only refunds the caller's ETH-gas-equivalent fee (`CallerDeposit`) — it never recovers the NEAR spent on `storage_deposit`. That NEAR is permanently and irrecoverably lost from the user's wallet account, with no function anywhere in the contract to reclaim or compensate for it, closely mirroring the `_mintToDao` bug class where value is moved to a place from which the intended owner has no way to retrieve it.

### Finding Description
`inner_rlp_execute` routes emulated ERC-20 transfers to unregistered receivers through `nep_141_storage_balance_callback`: [1](#0-0) 

When the receiver has no NEP-141 storage balance, the wallet builds a **single promise batch** on the token contract: `storage_deposit` (paid from the wallet's own account balance via `NEP_141_STORAGE_DEPOSIT_AMOUNT`) followed by the `ft_transfer`/`ft_transfer_call`, and only then `.then()`s into `rlp_execute_callback`: [2](#0-1) 

Because both `function_call`s are chained on the *same* `Promise::new(token_id)` object, they execute as two actions of a single receipt at the token contract. On NEAR, if a later action in a batch fails, the batch's outcome is reported as `Failed` to the awaiting callback, but any state changes already committed by earlier actions in that batch (here, the token contract registering the receiver's storage and consuming the deposit) are **not rolled back**. Only the failing/incomplete portion is undone from the caller's perspective.

`rlp_execute_callback` handles this failure case, but it only refunds `caller_deposit` — a value tracked separately for ETH-gas-fee compensation — and has no knowledge of, or refund path for, the `NEP_141_STORAGE_DEPOSIT_AMOUNT` that was already spent from the wallet's balance: [3](#0-2) 

`CallerDeposit` is populated purely from the transaction's fee/attached-deposit context and never includes the storage-deposit amount: [4](#0-3) 

There is no other method in the contract (`address_check_callback`, `ban_relayer`, or any public entrypoint) that can recover this spent balance. Once the batch executes with a successful `storage_deposit` and a failing `ft_transfer`, the ~0.00125 NEAR is gone from the wallet's account permanently, exactly as `_mintToDao` tokens were permanently stuck in `DaosLive` with no accessor function.

### Impact Explanation
Any relayer/user can trigger this by submitting (via `rlp_execute`, reachable through ordinary RPC/relayed transactions — no privileged access required) an RLP-encoded ERC-20 transfer whose target NEP-141 token is not yet registered for the receiver, where the `ft_transfer` step subsequently fails (e.g., insufficient token balance discovered only inside `ft_transfer`, a malicious/reverting token implementation, insufficient attached gas for the second action, or a receiver that rejects the transfer). Each such occurrence permanently drains `NEP_141_STORAGE_DEPOSIT_AMOUNT` (0.00125 NEAR) from the wallet's own balance with no refund and no recovery path. A malicious or buggy NEP-141 token contract could be crafted to always succeed `storage_deposit` while deterministically failing `ft_transfer`, letting an attacker repeatedly siphon NEAR from any eth-implicit wallet that attempts to transfer that token to unregistered receivers — a repeatable, uncompensated value loss for wallet owners.

### Likelihood Explanation
The precondition (receiver unregistered for the token + `ft_transfer` failing after `storage_deposit` succeeds) is easily and cheaply reproducible: an attacker fully controls the failure condition by deploying a NEP-141 contract whose `ft_transfer` always fails (e.g. `env::panic()`), and any wallet owner who attempts an ERC-20 transfer of that token to a new/unregistered address will hit this path automatically. No special privileges, timing, or race conditions are required — a single crafted transaction reliably triggers the loss.

### Recommendation
- Do not chain `storage_deposit` and the token transfer as two actions in a single receipt; instead, issue `storage_deposit` as its own promise and add a dedicated callback that verifies its success before issuing the transfer, refunding the deposit (or aborting before spending it) if any subsequent step fails.
- Alternatively, extend `CallerDeposit`/`rlp_execute_callback` to also track and refund the `NEP_141_STORAGE_DEPOSIT_AMOUNT` back to the wallet's own balance whenever the batch fails after the storage deposit action has already succeeded.
- Add integration tests asserting that a failing `ft_transfer` after a successful `storage_deposit` does not result in net NEAR loss for the wallet account.

### Proof of Concept
1. Deploy a malicious/buggy NEP-141 token contract `Evil141` whose `storage_deposit` succeeds normally but whose `ft_transfer` always fails (e.g., unconditional panic).
2. Fund an ETH-implicit wallet account (deployed with the Wallet Contract global code) with NEAR.
3. Have the wallet owner sign and submit (via a relayer, `rlp_execute`) an RLP transaction that is an ERC-20 `transfer` call to `Evil141`, targeting a `receiver_id` that has never called `storage_deposit` on `Evil141`.
4. `inner_rlp_execute` routes this through `nep_141_storage_balance_callback` at [1](#0-0) , which finds no storage balance and issues the two-action batch (`storage_deposit` paid from the wallet's balance, then `ft_transfer`).
5. `storage_deposit` succeeds (consumes `NEP_141_STORAGE_DEPOSIT_AMOUNT` from the wallet); `ft_transfer` fails as designed by `Evil141`.
6. `rlp_execute_callback` receives `PromiseResult::Failed` and refunds only `caller_deposit` (the ETH-gas-equivalent fee) at [3](#0-2) ; the `NEP_141_STORAGE_DEPOSIT_AMOUNT` is never returned to the wallet.
7. Observe the wallet's on-chain NEAR balance decreased by `NEP_141_STORAGE_DEPOSIT_AMOUNT` with no corresponding NEP-141 transfer having occurred and no code path to reclaim the loss — repeatable indefinitely against the same or other wallets.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L33-41)
```rust
const NEP_141_STORAGE_DEPOSIT_AMOUNT: NearToken = NearToken::from_yoctonear(1_250 * MICRO_NEAR);
const NEP_141_STORAGE_DEPOSIT_GAS: Gas = Gas::from_tgas(5);
const NEP_141_STORAGE_BALANCE_OF_GAS: Gas = Gas::from_tgas(5);
const REGISTRAR_LOOKUP_GAS: Gas = Gas::from_tgas(5);
const RLP_EXECUTE_CALLBACK_GAS: Gas = Gas::from_tgas(5);
const ADDRESS_CHECK_CALLBACK_GAS: Gas = Gas::from_tgas(5).saturating_add(RLP_EXECUTE_CALLBACK_GAS);
const NEP_141_STORAGE_BALANCE_CALLBACK_GAS: Gas = Gas::from_tgas(5)
    .saturating_add(NEP_141_STORAGE_DEPOSIT_GAS)
    .saturating_add(RLP_EXECUTE_CALLBACK_GAS);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L239-269)
```rust
            None => {
                // receiver_id is not registered so we must call `storage_deposit` first.
                let storage_deposit_args =
                    format!(r#"{{"account_id": "{receiver_id}"}}"#).into_bytes();
                let transfer_function_call = match action {
                    near_action::Action::FunctionCall(x) => x,
                    _ => {
                        return PromiseOrValue::Value(ExecuteResponse {
                            success: false,
                            success_value: None,
                            error: Some(
                                "Expected function call action to perform NEP-141 transfer".into(),
                            ),
                        });
                    }
                };
                Promise::new(token_id)
                    .function_call(
                        "storage_deposit".into(),
                        storage_deposit_args,
                        NEP_141_STORAGE_DEPOSIT_AMOUNT,
                        NEP_141_STORAGE_DEPOSIT_GAS,
                    )
                    .function_call(
                        transfer_function_call.method_name,
                        transfer_function_call.args,
                        transfer_function_call.deposit,
                        transfer_function_call.gas,
                    )
                    .then(ext.rlp_execute_callback(caller_deposit))
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-312)
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
```
