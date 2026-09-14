Based on my investigation, I found a genuine structural analog to the reported bug class in the NEAR wallet contract's NEP-141 handling logic.

### Title
Hardcoded NEP-141 storage deposit assumption in ETH Wallet Contract can cause token transfer failures or fund loss - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The wallet contract (used to let ETH-implicit accounts sign NEAR actions, reachable by any relayer/tx submitter via `rlp_execute`) hardcodes an assumed NEP-141 `storage_deposit` amount instead of querying the token contract's actual `storage_balance_bounds`, exactly mirroring the reported bug class where `getPriceOfAssetQuotedInUSD()` assumed fixed, universal properties (1:1 stablecoin peg, "W" naming convention) about heterogeneous external assets rather than querying their real values.

### Finding Description
`NEP_141_STORAGE_DEPOSIT_AMOUNT` is a hardcoded constant (`1_250 * MICRO_NEAR` yoctoNEAR) with an explicit comment stating the assumption: "This storage deposit value is the one used by the standard NEP-141 implementation, which essentially all tokens use. Therefore we hard-code it here instead of doing the extra on-chain call to `storage_balance_bounds`." [1](#0-0) 

This constant is used directly as the attached deposit in a cross-contract `storage_deposit` call whenever the wallet contract detects (via `nep_141_storage_balance_callback`) that a receiver is not yet registered with a NEP-141 token contract: [2](#0-1) 

The comment shows the developers were aware this is only true for "essentially all" (not all) tokens — the exact same class of reasoning error as the reported bug ("generally expected to be the case ... there have been instances where some ... failed to uphold" and "assumes that every token name that starts with 'W' is a wrapped token").

### Impact Explanation
If a NEP-141 token contract's actual `storage_balance_bounds().min` exceeds the hardcoded `1_250 * MICRO_NEAR` (non-standard tokens are permitted by the NEP-141 spec to set any bound), the `storage_deposit` cross-contract call will not satisfy the token contract's minimum, causing the chained `storage_deposit` → `ft_transfer`/token-method promise to fail. Since this occurs inside a batched promise chain initiated on behalf of the ETH-implicit account owner, the transaction fails and, per `rlp_execute_callback`, only the tracked `caller_deposit` (if any) is refunded — the yoctoNEAR sent for `storage_deposit` itself is not recoverable if the deposit is spent/rejected by the token contract's own logic, and repeated failed calls burn the user's attached gas and prepaid deposit on every relayed attempt. This is a fund-loss / broken-invariant condition triggered purely by interacting with a token contract that doesn't match the hardcoded assumption — reachable by any transaction signer routing a NEP-141 transfer for an unregistered receiver through the ETH wallet contract.

### Likelihood Explanation
The wallet contract is deployed as a global contract used by every ETH-implicit account on NEAR (mainnet/testnet), so the code path in `nep_141_storage_balance_callback` triggers on any ordinary NEP-141 transfer to a previously-unregistered receiver. Any token deviating from the exact `1_250 * MICRO_NEAR` bound (which the code's own comment concedes is not universal) will hit this path. Given the wide and growing variety of third-party NEP-141 token implementations, this is a realistic, non-adversarial trigger condition, not one requiring a malicious actor.

### Recommendation
Do not hardcode the NEP-141 storage deposit value. Instead, query the token contract's `storage_balance_bounds()` view method to obtain the accurate minimum deposit before calling `storage_deposit`, or attach a generously safe upper-bound deposit and refund the unused amount via the token contract's own storage-deposit refund mechanics (many NEP-141 implementations refund excess deposit). At minimum, treat a failed `storage_deposit` promise as a distinguishable, refundable failure so the user's funds are not silently lost when interacting with non-standard token contracts.

### Proof of Concept
1. Deploy a NEP-141 token contract that intentionally sets `storage_balance_bounds().min` higher than `1_250 * 10^18` yoctoNEAR (this is fully spec-compliant NEP-141 behavior — the standard does not mandate this exact value; see the code's own comment at [3](#0-2) ).
2. From an ETH-implicit account controlled by a user, submit an RLP-encoded transaction via `rlp_execute` targeting this token to transfer funds to a receiver account that has never called `storage_deposit` on that token.
3. The wallet contract's `inner_rlp_execute` flow calls `storage_balance_of`; since it returns `None`, `nep_141_storage_balance_callback` is invoked, which issues `Promise::new(token_id).function_call("storage_deposit", ..., NEP_141_STORAGE_DEPOSIT_AMOUNT, ...)` followed by the actual transfer call: [4](#0-3) 
4. Because the attached deposit is insufficient for this token's registration requirement, the `storage_deposit` call fails (or the underlying transfer subsequently fails since the account remains unregistered), causing the whole promise chain to fail; the caller's yoctoNEAR sent for `storage_deposit` is consumed/lost while the intended transfer never completes.

**Uncertainty note:** I could not fully trace the token-side rejection behavior (e.g., whether a specific token contract would panic and revert the deposit fully, or accept and register with a partial balance) since that logic lives in third-party NEP-141 contracts outside this repository's index. The core root cause — an explicit, acknowledged-as-imperfect hardcoded assumption about external NEP-141 token deposit requirements, avoiding an on-chain query — is confirmed directly in the sourced files above.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L26-34)
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
