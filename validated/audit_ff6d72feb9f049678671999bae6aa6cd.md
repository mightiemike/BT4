### Title
Native `tx.value` silently merged into ERC-20 `ft_transfer` deposit, permanently stuck in the token contract - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs`)

### Summary
The Wallet Contract emulates Ethereum transactions on NEAR. When an incoming RLP transaction's calldata is recognized as an ERC-20 `transfer(address,uint256)` call, `eth_emulation::try_emulation` builds a `FunctionCall` action targeting `ft_transfer` on the NEP-141 token contract with `yocto_near: 1` (the minimum required by the NEP-141 standard). Separately, the Ethereum transaction also carries a native `value` field. `parse_rlp_tx_to_action` unconditionally folds this `tx.value` into the action's deposit regardless of the action kind: [1](#0-0) 

`Action::try_into_near_action` then adds this `additional_value` on top of the fixed `yocto_near` for `FunctionCall` actions: [2](#0-1) 

This is exactly the OpenQ bug class: a call meant to move an "ERC20" token (`ft_transfer`) also silently carries a native-token payment (`tx.value` → NEAR deposit), and there is no rejection of the combination.

### Finding Description
There is no validation anywhere in `parse_tx_data` / `eth_emulation::try_emulation` / `parse_rlp_tx_to_action` that rejects a nonzero `tx.value` when the transaction is being emulated as an ERC-20 `transfer`. `validate_tx_value` only bounds the maximum size of `tx.value`, it does not check that `tx.value == 0` for the `ERC20Transfer` emulation kind: [3](#0-2) 

As a result, a user (or relayer acting on the user's signed transaction) can construct an RLP transaction whose `data` selects `ERC20_TRANSFER_SELECTOR` (calling `ft_transfer` on the token contract) while also setting a nonzero `value`. The Wallet Contract will:
1. Build the `Action::FunctionCall` with `receiver_id = token_contract`, `method_name = "ft_transfer"`, `yocto_near = 1`.
2. Add `tx.value` (converted to NEAR yoctoNear) into the final deposit via `try_into_near_action`.
3. Dispatch a promise carrying that inflated deposit to the token contract's `ft_transfer` method: [4](#0-3) 

Standard NEP-141 `ft_transfer` implementations only require `assert_one_yocto()` and do not refund any deposit beyond 1 yoctoNEAR to the caller (the wallet contract) — the excess deposit becomes part of the token contract's own NEAR balance, permanently unreachable by the user who intended it as a value transfer. This mirrors the OpenQ report precisely: the "native token" (NEAR) sent alongside an "ERC20-style" call is not accounted for as a refundable/native transfer, and is effectively lost to the funder while the token contract silently absorbs it. The `caller_deposit`/refund-on-failure mechanism in `rlp_execute_callback` only refunds the deposit if the promise itself fails; if `ft_transfer` succeeds (the common case) the excess NEAR is not returned: [5](#0-4) 

### Impact Explanation
Any NEAR sent as `tx.value` in a transaction whose calldata resolves to an ERC-20 `transfer` emulation is deposited into the token contract instead of being transferred to the intended recipient or refunded to the wallet owner. This is a permanent loss of funds for the wallet contract's owner (unauthorized value movement / permanently frozen funds), reachable purely via a single signed RLP transaction routed through `rlp_execute` — no privileged, validator, or network-level access is required. Given the Wallet Contract is a core primitive for Ethereum-style accounts on NEAR (aurora / eth-implicit accounts), this can affect any user of that feature.

### Likelihood Explanation
Likelihood is high for accidental loss (a wallet/dApp author building an Ethereum-style transaction that naively sets both `value` and ERC-20 `transfer` calldata, believing `value` behaves like it does for a plain ETH transfer) and plausible for intentional exploitation if a relayer or malicious frontend crafts such a transaction to strip value from a user, since the user's signature only covers the RLP transaction bytes and the emulation-to-deposit mapping is entirely determined by wallet-contract logic the user cannot easily preview.

### Recommendation
In `eth_emulation::try_emulation`'s `ERC20_TRANSFER_SELECTOR` branch (and other emulation branches such as `ERC20_BALANCE_OF_SELECTOR`/`ERC20_TOTAL_SUPPLY_SELECTOR` where a deposit makes no sense at all), explicitly reject the case where `tx.value` is nonzero, e.g. by validating `tx.value.raw().is_zero()` before constructing the `ParsableEthEmulationKind::ERC20Transfer` action, returning a `UserError` (e.g. `UnexpectedValue`) otherwise. Alternatively, only allow `try_into_near_action`'s `additional_value` folding for `Action::Transfer`/native `FunctionCall` kinds and forbid it for `EthEmulationKind::ERC20Transfer`.

### Proof of Concept
1. Deploy the Wallet Contract for an eth-implicit account `0xabc...` and give it a NEP-141 token balance on `token.near`.
2. Craft an RLP-encoded Ethereum transaction with `to = token.near`'s corresponding eth-implicit representation, `data = ERC20_TRANSFER_SELECTOR || abi.encode(to, amount)`, and `value = N` (nonzero, within `VALUE_MAX`).
3. Sign it with the wallet's key and submit via `rlp_execute` (as in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/emulation.rs`, but adding a nonzero `value`).
4. Observe: `parse_rlp_tx_to_action` returns a `FunctionCall` to `token.near::ft_transfer` with deposit `= 1 + N*MAX_YOCTO_NEAR` yoctoNEAR instead of `1`.
5. `ft_transfer` succeeds (only requires ≥1 yoctoNEAR), the extra `N*MAX_YOCTO_NEAR` NEAR becomes part of `token.near`'s balance, and there is no code path that transfers or refunds it back to the wallet owner or intended recipient — the value is permanently lost.

Note: I could not run the actual test harness in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/emulation.rs` to empirically confirm the exact balance delta (only static code reading was possible within tool access); the reasoning above is based directly on the code paths cited.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L159-163)
```rust
    validate_tx_value(&tx)?;

    // Call to `low_u128` here is safe because of the validation done in `validate_tx_value`
    let near_action = action
        .try_into_near_action(tx.value.raw().low_u128().saturating_mul(MAX_YOCTO_NEAR.into()))?;
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L370-376)
```rust
fn validate_tx_value(tx: &NormalizedEthTransaction) -> Result<(), Error> {
    if tx.value.raw() > VALUE_MAX {
        return Err(Error::User(UserError::ValueTooLarge));
    }

    Ok(())
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L243-253)
```rust
            Action::FunctionCall { receiver_id: _, method_name, args, gas, yocto_near } => {
                let action = FunctionCallAction {
                    method_name,
                    args,
                    gas: Gas::from_gas(gas),
                    deposit: NearToken::from_yoctonear(
                        additional_value.saturating_add(yocto_near.into()),
                    ),
                };
                near_action::Action::FunctionCall(action)
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L475-483)
```rust

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
