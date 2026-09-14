### Title
Excess `attached_deposit` forwarded to a NEP-141 ERC-20-emulated `ft_transfer` (yocto_near hard-coded to `1`) is permanently lost when the caller mistakenly attaches native NEAR - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs`)

### Summary
The Wallet Contract's ERC-20 emulation path always constructs the underlying `ft_transfer` `FunctionCall` action with a hard-coded `yocto_near: 1` regardless of how much NEAR the external caller actually attached to `rlp_execute`. The remaining attached deposit is neither forwarded to the token contract nor refunded on success, so it is silently absorbed into the wallet contract's own balance whenever the emulated transaction succeeds.

### Finding Description
`rlp_execute` is `#[payable]` [1](#0-0) , and any attached deposit is captured in `context.attached_deposit` and tracked via `CallerDeposit::new` purely so it can be **refunded on failure**: [2](#0-1) .

When the transaction is parsed as an ERC-20 `transfer(...)` call, `eth_emulation::try_emulation` builds the resulting Near `ft_transfer` action with `yocto_near` **hard-coded to `1`**, independent of `context.attached_deposit`: [3](#0-2) . The only place `context.attached_deposit` is otherwise used is to compute `tx.value` (the emulated ETH transfer value) via `try_into_near_action`, which is a separate, distinct NEAR transfer amount encoded in the transaction payload, not the raw attached deposit itself: [4](#0-3) .

On the success path, `rlp_execute_callback` only refunds the caller's deposit if the promise result is `PromiseResult::Failed`; on `PromiseResult::Successful`, the tracked `caller_deposit` is simply dropped without any transfer back: [5](#0-4) . Since the `Promise::new(token_id).function_call(...)` created for the `ft_transfer` (and the preceding `storage_balance_of`/`storage_deposit` calls) never move the caller's attached deposit anywhere else, and only exactly `1 yoctoNEAR` (the fixed value baked into the action) is ever attached to the outbound promise, any additional NEAR attached by the caller stays on the Wallet Contract's own balance permanently — the caller has no way to reclaim it, mirroring the reported `receiveFunds`-style bug class where a native-value transfer accompanying a token-only operation is not tracked or refunded.

### Impact Explanation
Any external caller (an unprivileged relayer's `msg.sender`, or even a user directly submitting a `rlp_execute` transaction with the ERC-20 `transfer` selector) who attaches non-trivial NEAR to the call — for example, believing it will be forwarded as `msg.value` in the Ethereum semantics being emulated, or simply making a mistake — will have that NEAR permanently locked in the Wallet Contract, with no path to a refund once the underlying `ft_transfer` succeeds. This is a genuine, transaction-triggered, unauthorized value loss / permanently frozen funds scenario for the caller, reachable by a single ordinary RPC-submitted transaction with no special privileges.

### Likelihood Explanation
Likelihood is moderate: it requires a caller who is unaware that `yocto_near` for ERC-20 emulation is fixed at `1` and attaches deposit to the `rlp_execute` call. Given the ETH/EVM mental model this contract is deliberately trying to emulate (users are used to attaching `msg.value`, or relayers packaging fee/value together), such a mistake is plausible, especially given the codebase already anticipates and refunds an analogous mistake for relayer `fee` handling but not for the raw attached deposit on success.

### Recommendation
Either (a) reject the `rlp_execute` call if `context.attached_deposit` exceeds the amount actually consumed by the derived action (mirroring the `receiveFunds` fix of enforcing `msg.value == 0`/expected amount for non-native-transfer operations), or (b) always refund `caller_deposit` minus whatever amount was legitimately forwarded/consumed by the action, in both the success and failure branches of `rlp_execute_callback`, not just on failure.

### Proof of Concept
1. Deploy a Wallet Contract and a NEP-141 token, register the wallet with the token, and mint tokens to the wallet.
2. Construct and sign an RLP Ethereum transaction with the `ERC20_TRANSFER_SELECTOR` calling `transfer(to, value)` on the token's mapped address, per `eth_emulation::try_emulation`.
3. Call `rlp_execute(target, tx_bytes_b64)` attaching e.g. `1 NEAR` as the deposit (more than the hard-coded `1 yoctoNEAR` used for the `ft_transfer` storage-deposit-compatible call).
4. Observe that the `ft_transfer` succeeds (tokens move to the recipient) but the extra ~`1 NEAR - 1 yoctoNEAR` attached by the caller remains on the Wallet Contract's own account balance — `rlp_execute_callback`'s `PromiseResult::Successful` branch performs no refund of `caller_deposit`: [6](#0-5) .

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-93)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-192)
```rust
impl CallerDeposit {
    pub fn new(context: &ExecutionContext) -> Option<Self> {
        // Only track for external (non-self) callers
        if context.current_account_id == context.predecessor_account_id {
            return None;
        }

        NonZeroU128::new(context.attached_deposit.as_yoctonear()).map(|yocto_near| Self {
            account_id: context.predecessor_account_id.clone(),
            yocto_near,
        })
    }
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs (L83-92)
```rust
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L159-165)
```rust
    validate_tx_value(&tx)?;

    // Call to `low_u128` here is safe because of the validation done in `validate_tx_value`
    let near_action = action
        .try_into_near_action(tx.value.raw().low_u128().saturating_mul(MAX_YOCTO_NEAR.into()))?;

    Ok((near_action, transaction_kind))
```
