### Title
Caller Deposit Silently Lost When `storage_balance_of` Fails in `nep_141_storage_balance_callback` — (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary

The `near-wallet-contract`'s ERC-20 emulation path calls `storage_balance_of` on the target NEP-141 token contract before executing `ft_transfer`. NEP-145 (storage management) is not mandatory for all NEP-141 tokens. When `storage_balance_of` fails — because the token does not implement NEP-145 — `nep_141_storage_balance_callback` returns early with an error response and **never schedules `rlp_execute_callback`**. The only place where the relayer's attached NEAR deposit (`caller_deposit`) is refunded is inside `rlp_execute_callback`. Because that callback is never reached, the deposit is permanently absorbed by the wallet contract and the relayer loses their funds.

### Finding Description

The ERC-20 transfer flow in `inner_rlp_execute` (lib.rs lines 433–457) builds a two-step promise chain:

```
token_contract::storage_balance_of(receiver_id)
  .then(self::nep_141_storage_balance_callback(..., caller_deposit))
``` [1](#0-0) 

`caller_deposit` is constructed from the relayer's `attached_deposit` at the time `rlp_execute` is called: [2](#0-1) 

Inside `nep_141_storage_balance_callback`, when `storage_balance_of` returns `PromiseResult::Failed`, the function returns immediately with an error value — it does **not** schedule `rlp_execute_callback`: [3](#0-2) 

The same early-return pattern also fires when the response cannot be deserialized (lines 213–219): [4](#0-3) 

The **only** place where `caller_deposit` is refunded is `rlp_execute_callback` lines 297–305: [5](#0-4) 

Because neither early-return path in `nep_141_storage_balance_callback` schedules `rlp_execute_callback`, the deposit is never returned. The wallet contract retains the NEAR tokens with no mechanism to recover them.

The same structural defect exists in `address_check_callback` at lines 142–147 and 151–157, where early returns also skip `rlp_execute_callback` and silently drop `caller_deposit`. [6](#0-5) 

### Impact Explanation

The relayer's attached NEAR deposit — which is required to be non-zero for `ft_transfer` (NEP-141 mandates 1 yoctoNEAR) and may be larger for other function-call actions — is permanently lost inside the wallet contract. The wallet contract has no withdrawal or recovery function. The invariant stated in the code's own comment ("The cross-contract call failed, refund the caller if needed") is violated for the `storage_balance_of` failure path. This constitutes a direct, irreversible **loss of funds** for the relayer.

### Likelihood Explanation

NEP-145 (storage management) is a separate standard from NEP-141 (fungible tokens). The wallet contract's own comment acknowledges this: *"which essentially all tokens use"* — implying not all do. [7](#0-6) 

Any NEP-141 token that omits `storage_balance_of` will trigger this path. A user only needs to attempt an ERC-20 emulated transfer of such a token through the wallet contract with a non-zero relayer deposit. No privileged access is required.

### Recommendation

In both `nep_141_storage_balance_callback` and `address_check_callback`, replace the bare early-return `PromiseOrValue::Value(...)` paths with logic that first issues a refund transfer to `caller_deposit.account_id` before returning, mirroring the pattern already used in `rlp_execute_callback`:

```rust
// Before returning an error, refund the caller deposit
if let Some(CallerDeposit { account_id, yocto_near }) = caller_deposit {
    let refund = env::promise_batch_create(&account_id);
    env::promise_batch_action_transfer(
        refund,
        NearToken::from_yoctonear(yocto_near.into()),
    );
}
return PromiseOrValue::Value(ExecuteResponse { success: false, ... });
```

Alternatively, refactor all error paths to always go through `rlp_execute_callback` so the refund logic is centralised and cannot be accidentally bypassed.

### Proof of Concept

1. Deploy a NEP-141 token contract on NEAR that does **not** implement `storage_balance_of` (i.e., omits NEP-145).
2. Mint tokens to an ETH-implicit wallet-contract account.
3. As a relayer, call `rlp_execute` on the wallet contract with an ERC-20 transfer transaction targeting the token contract, attaching e.g. 1 yoctoNEAR as deposit.
4. The wallet contract calls `storage_balance_of` on the token contract → the call fails (method not found).
5. `nep_141_storage_balance_callback` fires with `PromiseResult::Failed`, hits the early-return at line 205, and returns `ExecuteResponse { success: false, ... }` without scheduling `rlp_execute_callback`.
6. The relayer's 1 yoctoNEAR (or larger) deposit is now held by the wallet contract with no recovery path.

The exact corrupted value is `caller_deposit.yocto_near` — the full attached deposit of the relayer — which is absorbed by the wallet contract instead of being returned. [8](#0-7)

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L28-33)
```rust
/// This storage deposit value is the one used by the standard NEP-141 implementation,
/// which essentially all tokens use. Therefore we hard-code it here instead of doing
/// the extra on-chain call to `storage_balance_bounds`. This also prevents malicious
/// token contracts with very high `storage_balance_bounds` from taking lots of $NEAR
/// from eth-wallet-contract users.
const NEP_141_STORAGE_DEPOSIT_AMOUNT: NearToken = NearToken::from_yoctonear(1_250 * MICRO_NEAR);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-157)
```rust
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from account registrar".into()),
                    });
                }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-273)
```rust
    #[private]
    pub fn nep_141_storage_balance_callback(
        &mut self,
        token_id: AccountId,
        receiver_id: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
            PromiseResult::Successful(value) => match serde_json::from_slice(&value) {
                Ok(x) => x,
                Err(_) => {
                    return PromiseOrValue::Value(ExecuteResponse {
                        success: false,
                        success_value: None,
                        error: Some("Unexpected response from NEP-141 storage_balance_of".into()),
                    });
                }
            },
        };
        let current_account_id = env::current_account_id();
        let ext = WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
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
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-305)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L445-457)
```rust
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L180-191)
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
```
