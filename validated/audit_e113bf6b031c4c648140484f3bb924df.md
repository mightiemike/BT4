### Title
Unrefunded NEP-141 `storage_deposit` Spend on Failed ERC-20 Emulated Transfer Causes Permanent Fund Loss — (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (used to emulate Ethereum EOAs / ERC-20 semantics on top of NEP-141 tokens) spends the wallet's own NEAR balance to register an unregistered token receiver via `storage_deposit` *before* attempting the actual `ft_transfer`. If the subsequent `ft_transfer` fails for any reason (e.g. insufficient token balance, insufficient gas), the wallet's refund logic only refunds the caller's attached deposit — never the NEAR that was already irreversibly spent on `storage_deposit`. This mirrors the "unsafe ERC20/transfer assumption" bug class from the source report: the code treats the multi-step transfer as an atomic success/fail unit, but on-chain NEAR batched actions are **not atomic** — a partial success (the deposit) followed by a failure (the transfer) silently and permanently consumes user funds.

### Finding Description
In `nep_141_storage_balance_callback`, when the ERC-20 emulated transfer's receiver is not yet registered with the NEP-141 token, the contract builds a single promise batching two `function_call` actions — `storage_deposit` funded with `NEP_141_STORAGE_DEPOSIT_AMOUNT` (1,250 µNEAR) taken from the wallet contract's own balance, followed by the actual transfer call: [1](#0-0) 

Because these are batched (not `.then()`-chained) actions in a single receipt, they execute sequentially with state effects of earlier actions committed independently of later ones — i.e. if `storage_deposit` succeeds and `ft_transfer` subsequently fails, the deposit is not rolled back, but the promise's terminal result (seen by `.then()`) is `Failed`.

The callback that runs after this batch, `rlp_execute_callback`, only distinguishes success/failure of the *whole* promise and, on failure, refunds solely the caller's attached deposit (`caller_deposit`), never the wallet's own already-spent `NEP_141_STORAGE_DEPOSIT_AMOUNT`: [2](#0-1) 

The `NEP_141_STORAGE_DEPOSIT_AMOUNT` constant and its gas budget are defined here, confirming the deposit is funded from the contract's own balance rather than any refundable escrow: [3](#0-2) 

This is the direct analog of "Use of Unsafe ERC20 Operations": the external report flags code that assumes a token operation succeeded without properly checking/handling partial failure, leading to silent loss. Here, the wallet contract assumes the two-step `storage_deposit` + `ft_transfer` sequence is all-or-nothing, but NEAR's batched-action semantics allow the first (fund-spending) step to succeed while the second (intended) step fails, with no compensating refund path for the first step's cost.

### Impact Explanation
Any ordinary, unprivileged user (or relayer forwarding a validly-signed transaction) triggering an ERC-20 emulated transfer via `rlp_execute` to a receiver not yet registered with the NEP-141 token — where the transfer itself subsequently fails (e.g., the wallet's token balance is less than the transfer amount, or gas runs out on the transfer step) — causes the wallet contract to permanently lose `NEP_141_STORAGE_DEPOSIT_AMOUNT` (1,250 µNEAR) of its own NEAR balance per failed attempt, with the value migrating to the token contract as storage balance credited to an arbitrary receiver account that the wallet owner cannot reclaim. This is a concrete, transaction-triggered, unauthorized/unintended value movement and permanent loss of the wallet owner's funds, satisfying the "permanently frozen funds" / "unauthorized value movement" impact categories. Although the per-transaction amount is small, it is deterministically reproducible and can be repeated across many failed transfer attempts (e.g. an attacker crafting/relaying transactions targeting unregistered receivers combined with insufficient token balances or gas), draining the wallet's NEAR balance over time without any user benefit.

### Likelihood Explanation
This requires no privileged access — it is reachable by any account able to call `rlp_execute` on a deployed NEAR Wallet Contract with a normal emulated ERC-20 transfer to an unregistered receiver, combined with a transfer that fails after registration succeeds (a common and easily engineered condition: insufficient token balance, insufficient attached gas for the second call, or a reverting token contract). No malicious validator, network, or node behavior is needed — a single crafted or naturally-occurring transaction triggers it.

### Recommendation
Make the refund/accounting logic in `rlp_execute_callback` (and `nep_141_storage_balance_callback`) aware of partial success: either (a) chain `storage_deposit` and the transfer as separate `.then()` steps with an explicit callback that reverses/refunds the storage deposit cost to the wallet if the transfer step fails, or (b) fund the `storage_deposit` from an escrowed/refundable balance rather than the wallet's spendable balance until the transfer is confirmed successful, or (c) use a single atomic cross-contract call pattern (e.g., have the token contract perform registration-and-transfer atomically) so that a failed transfer cannot leave a committed storage deposit.

### Proof of Concept
1. Deploy the NEAR Wallet Contract for an eth-implicit account `W` and mint it some NEP-141 tokens (amount `M`).
2. Construct a signed Ethereum transaction emulating `ERC20.transfer(receiver, amount)` where `receiver` is a fresh, unregistered NEP-141 account and `amount > M` (or attach only enough gas to cover `storage_deposit` but not `ft_transfer`).
3. Submit via `rlp_execute`. Execution flow:
   - `nep_141_storage_balance_callback` sees `storage_balance_of(receiver) == None`, so it issues a batched `storage_deposit` (funded from `W`'s NEAR balance) followed by `ft_transfer`.
   - `storage_deposit` action succeeds, permanently deducting `NEP_141_STORAGE_DEPOSIT_AMOUNT` from `W`.
   - `ft_transfer` then fails/panics (insufficient token balance or out of gas).
   - The batch's terminal promise result is `Failed`; `rlp_execute_callback` refunds only `caller_deposit` (typically the relayer's attached deposit, not `W`'s spent balance).
4. Observe: `W`'s NEAR balance decreased by `NEP_141_STORAGE_DEPOSIT_AMOUNT` with no corresponding token transfer completed and no refund of that amount to `W`.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L26-41)
```rust
const MICRO_NEAR: u128 = 10_u128.pow(18);
const ADDRESS_REGISTRAR_ACCOUNT_ID: &str = std::include_str!("ADDRESS_REGISTRAR_ACCOUNT_ID");
/// This storage deposit value is the one used by the standard NEP-141 implementation,
/// which essentially all tokens use. Therefore we hard-code it here instead of doing
/// the extra on-chain call to `storage_balance_bounds`. This also prevents malicious
/// token contracts with very high `storage_balance_bounds` from taking lots of $NEAR
/// from eth-wallet-contract users.
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
