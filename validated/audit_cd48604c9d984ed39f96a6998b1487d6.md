## Title
Wallet-contract ERC-20↔NEP-141 emulation reports transaction success from bare promise success, without validating that the underlying token transfer actually moved funds — ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
`near-wallet-contract` emulates Ethereum ERC-20 `transfer()` calls by translating them into NEP-141 `ft_transfer` calls on an arbitrary target contract, and reports the Ethereum-style transaction as successful purely based on whether the resulting NEAR promise resolved without panicking — never validating that value was actually moved. This mirrors the exact "no revert on failure" pattern in the original report: a call that returns normally (instead of reverting/panicking) is treated as proof of a successful transfer.

### Finding Description
`try_emulation` in `eth_emulation.rs` converts an ERC-20 `transfer(to, value)` selector into a NEP-141 `ft_transfer` function call action targeting the token contract given by the user-supplied `target` account: [1](#0-0) 

That action is dispatched as a cross-contract promise, and its outcome is judged solely by `rlp_execute_callback`, which treats *any* non-panicking (`PromiseResult::Successful`) result as a successful transfer, setting `success: true` in the `ExecuteResponse` returned to the caller: [2](#0-1) 

The wallet contract never queries the token's balance before/after the call, nor inspects any structured return payload — it only checks that the promise did not fail. This is precisely the "no revert on failure" trust assumption from the report: on Ethereum, some ERC-20 tokens signal failure by returning `false` instead of reverting, and a caller that only checks "did the call revert?" (not the return value) will wrongly conclude the transfer succeeded. Here, the wallet contract only checks "did the NEAR promise fail/panic?" and treats a normal (non-panicking) resolution as conclusive proof that `value` was actually moved to `to` — regardless of whatever the target contract's business logic actually did.

Before the promise even resolves, the nonce has already been incremented in `inner_rlp_execute` (to prevent replay), and if a non-zero fee was specified the refund to the relayer is unconditionally scheduled at this point too: [3](#0-2) 

So once the transaction is submitted, the user's Ethereum-style nonce is consumed and cannot be replayed, and the relayer fee is paid, independent of whether the target contract's `ft_transfer` truly transferred the tokens. The only gate against loss is that the target contract must *revert on failure* per NEP-141 semantics — an assumption about external contract behavior identical in spirit to the ERC-20 "assume revert on failure" assumption broken in the original report.

The same blind trust also exists in `nep_141_storage_balance_callback`; it forwards the `ft_transfer` call whenever `storage_balance_of` returns `Some(_)`, and again finalizes success purely from the raw promise-success/failure signal in `rlp_execute_callback`: [4](#0-3) 

### Impact Explanation
A relayer or the eth-wallet owner can submit an RLP-encoded "ERC-20 transfer" transaction against any NEAR account claiming to be a NEP-141 token (the `target` is attacker/user-controlled and not restricted to a known-good token registry). If that target contract returns normally without panicking yet does not actually debit/credit balances (deviating from strict NEP-141 panic-on-failure semantics, analogous to a "no revert on failure" ERC-20), the wallet contract will: (1) irreversibly consume the nonce, (2) pay out any relayer fee, and (3) report `ExecuteResponse{ success: true }` to the caller/RPC client — even though the intended value transfer never happened. This is a direct on-chain analog of "the token is recognized as withdrawn even though it has not been withdrawn," resulting in silent, unauthorized-outcome loss of value for the wallet owner with no on-chain trace of failure.

### Likelihood Explanation
Reachable by any unprivileged transaction signer/relayer through the public `rlp_execute` entry point with an arbitrary `target` account of their choosing; no privileged role is required. It depends on interacting with a non-strictly-compliant NEP-141 contract (compliant tokens panic on failure, which the wallet contract does correctly detect), so likelihood is moderate — but the wallet contract provides no defense-in-depth (e.g., balance verification) against this class of target, unlike a `safeTransfer`-style check.

### Recommendation
Do not treat bare promise success as proof of transfer completion for emulated ERC-20/NEP-141 transfers. After the `ft_transfer` promise resolves successfully, issue a follow-up `ft_balance_of` check (or otherwise validate the state change) before reporting `success: true` and before finalizing nonce/fee effects, or otherwise document/restrict which token contracts are eligible for emulation (e.g., a registry of audited NEP-141 implementations) to guarantee panic-on-failure semantics.

### Proof of Concept
1. Deploy a NEAR contract at some `token_id` account that exposes `ft_transfer(receiver_id, amount, memo)` but returns normally (no panic) without actually updating balances when, e.g., the sender has insufficient balance (deviating from strict NEP-141).
2. From the eth-wallet owner, submit an RLP-encoded Ethereum "ERC-20 transfer" transaction with `target = token_id`, calling `ERC20_TRANSFER_SELECTOR` for more tokens than the wallet's tracked balance in that contract.
3. `try_emulation` (`eth_emulation.rs:59-93`) converts this into an `ft_transfer` function call, which is dispatched via `rlp_execute` → `inner_rlp_execute`. The nonce is incremented immediately (`lib.rs:364`) and any fee is paid.
4. The token contract's `ft_transfer` executes without panicking (per the faulty implementation) and the promise resolves as `PromiseResult::Successful`.
5. `rlp_execute_callback` (`lib.rs:296-316`) returns `ExecuteResponse{ success: true, ... }` even though no tokens were actually moved, and the nonce cannot be reused to retry the transfer.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs (L59-93)
```rust
        ERC20_TRANSFER_SELECTOR => {
            // We intentionally map to `u128` instead of `U256` because the NEP-141 standard
            // is to use u128.
            let (to, value): (Address, u128) =
                ethabi_utils::abi_decode(&ERC20_TRANSFER_SIGNATURE, &tx.data[4..])?;
            let receiver_id: AccountId = format!("0x{}{}", hex::encode(to), suffix)
                .parse()
                .unwrap_or_else(|_| env::panic_str("eth-implicit accounts are valid account ids"));

            // Include any data after the main args as a memo in the transfer.
            // The main data takes 68 bytes because there is a 4-byte selector followed
            // by two arguments which are each allocated 32 bytes according to the
            // Solidity ABI standard.
            let memo = if tx.data.len() > 68 {
                Some(format!(r#""0x{}""#, hex::encode(&tx.data[68..])))
            } else {
                None
            };
            let args = format!(
                r#"{{"receiver_id": "{}", "amount": "{}", "memo": {}}}"#,
                receiver_id.as_str(),
                value,
                memo.as_deref().unwrap_or("null"),
            );
            Ok((
                Action::FunctionCall {
                    receiver_id: target.to_string(),
                    method_name: "ft_transfer".into(),
                    args: args.into_bytes(),
                    gas: 2 * FIVE_TERA_GAS,
                    yocto_near: 1,
                },
                ParsableEthEmulationKind::ERC20Transfer { receiver_id, fee },
            ))
        }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L224-238)
```rust
        let promise = match maybe_storage_balance {
            Some(_) => {
                // receiver_id is registered so we can send the transfer
                // without additional actions. Note: in the standard NEP-141
                // implementation it is impossible to have `Some` storage balance,
                // but have it be insufficient to transact.
                match action_to_promise(token_id, action)
                    .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
                {
                    Ok(p) => p,
                    Err(e) => {
                        return PromiseOrValue::Value(e.into());
                    }
                }
            }
```

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
