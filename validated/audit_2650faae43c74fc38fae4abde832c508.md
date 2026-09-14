## Analog Found

### Title
Hardcoded static gas budgets for cross-contract calls in the NEAR Wallet Contract can permanently strand caller deposits if gas costs change - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The Solidity report flags `fillQuote()` for using `transfer()`, which hardcodes a fixed 2300-gas stipend that can silently break if gas costs change. The NEAR Wallet Contract (`runtime/near-wallet-contract/implementation/wallet-contract`) has a direct structural analog: it hardcodes fixed `Gas` constants for cross-contract calls to *external, untrusted* accounts (the address registrar and arbitrary NEP-141 token contracts), and for at least two of those calls, failure due to running out of that fixed gas budget results in the caller's attached deposit never being refunded.

### Finding Description
The wallet contract defines several compile-time-fixed gas constants used to fund cross-contract calls: [1](#0-0) 

These are attached via `.with_static_gas(...)` to calls that leave the wallet contract's control and are executed by arbitrary external contracts:
- `REGISTRAR_LOOKUP_GAS` (5 Tgas) funds a call to the address registrar contract: [2](#0-1) 
- `NEP_141_STORAGE_BALANCE_OF_GAS` (5 Tgas) funds a call to `storage_balance_of` on an arbitrary, user-supplied NEP-141 token contract (`target`): [3](#0-2) 

Just like Solidity's `transfer()`, which assumes 2300 gas will always be enough for a receive/fallback and breaks when EVM gas-cost repricing (e.g. EIP-1884) increases the cost of the same opcodes, these NEAR constants assume today's `1ms = 1Tgas` wasm cost table will always be enough for `storage_balance_of`/`lookup` on an arbitrary external contract. If a future protocol version reprices wasm host functions/storage reads (raising the gas cost of the same logical operation, as has already happened historically per `docs/architecture/how/gas.md`), or if a legitimately-implemented (but more storage/read-heavy) NEP-141 contract needs more than 5 Tgas to answer `storage_balance_of`, the promise will fail with an out-of-gas `PromiseResult::Failed`.

Crucially, the callbacks that handle these two specific failures do **not** refund the caller's `CallerDeposit`, unlike the final `rlp_execute_callback` which explicitly refunds on failure: [4](#0-3) [5](#0-4) 

Compare with the only path that does refund on failure: [6](#0-5) 

The `CallerDeposit` mechanism exists specifically to make external callers whole when a cross-contract call fails: [7](#0-6) 

and is verified as working in the "happy path" failure case by `test_caller_refunds`: [8](#0-7) 

But that test only exercises failure of the *final* action call (routed through `rlp_execute_callback`), not failure of the intermediate `lookup`/`storage_balance_of` calls funded by the hardcoded gas constants.

### Impact Explanation
If `REGISTRAR_LOOKUP_GAS` or `NEP_141_STORAGE_BALANCE_OF_GAS` becomes insufficient (due to a protocol-level gas repricing, or simply because the external contract's `storage_balance_of` needs more compute than assumed), any external caller (relayer or user submitting an RLP-encoded Ethereum-style transaction through `rlp_execute`) who attaches a NEAR deposit for an emulated base-token transfer to an unregistered address, or an ERC-20 transfer to an unregistered account, will have that call fail with `PromiseResult::Failed` inside `address_check_callback`/`nep_141_storage_balance_callback` — and their attached deposit is silently retained by the wallet contract with no refund path. This is a permanent loss of user funds (frozen/stuck funds), directly reachable by any ordinary transaction signer or relayer interacting with the Wallet Contract, with no privileged access required.

### Likelihood Explanation
This requires either (a) a future protocol gas-cost repricing that raises the effective cost of the wasm operations performed inside `lookup`/`storage_balance_of` beyond 5 Tgas, or (b) interaction with an NEP-141 token contract whose `storage_balance_of` legitimately needs more than 5 Tgas (e.g., due to additional lookups, larger account IDs, or future NEP-141 implementations with heavier logic). Given that nearcore's own gas documentation notes wasm cost parameters are periodically re-estimated and adjusted, and that the wallet contract is meant to interoperate with arbitrary, unauditable third-party NEP-141 contracts, this is a realistic occurrence, not a purely theoretical one — mirroring the real-world EIP-1884 repricing that broke `transfer()`-based contracts.

### Recommendation
- Replace hardcoded static gas constants (`REGISTRAR_LOOKUP_GAS`, `NEP_141_STORAGE_BALANCE_OF_GAS`, and related derived constants) for calls into external/arbitrary contracts with a weight-based or "attach remaining gas" strategy (e.g., `promise_batch_action_function_call_weight`, already used elsewhere in nearcore, see `docs/architecture/how/gas.md`), so the actual gas available scales with what's attached to the top-level call rather than a fixed assumption.
- Ensure the `Failed` branches of `address_check_callback` and `nep_141_storage_balance_callback` refund `CallerDeposit` exactly as `rlp_execute_callback` does, so an out-of-gas or otherwise-failing intermediate call cannot strand user funds.

### Proof of Concept
1. A user (or relayer on their behalf) calls `rlp_execute` on their Wallet Contract instance with an RLP-encoded ERC-20 `transfer` to a `receiver_id` that is not yet registered with the target NEP-141 token, attaching a NEAR deposit (`CallerDeposit` is created since predecessor ≠ current account, per `CallerDeposit::new`).
2. `inner_rlp_execute` schedules `storage_balance_of` on the token contract funded with the fixed `NEP_141_STORAGE_BALANCE_OF_GAS` (5 Tgas), chained to `nep_141_storage_balance_callback`.
3. Assume a future protocol gas repricing (or a token contract with heavier `storage_balance_of` logic) makes 5 Tgas insufficient for that call to complete.
4. The promise fails with `PromiseResult::Failed`; `nep_141_storage_balance_callback` hits the `Failed` arm, which returns an error response but never inspects/refunds `caller_deposit`.
5. The user's attached deposit remains in the Wallet Contract's balance permanently, with no code path to reclaim it — a transaction-triggered, unauthorized-appearing loss/freezing of user funds.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L34-41)
```rust
const NEP_141_STORAGE_DEPOSIT_GAS: Gas = Gas::from_tgas(5);
const NEP_141_STORAGE_BALANCE_OF_GAS: Gas = Gas::from_tgas(5);
const REGISTRAR_LOOKUP_GAS: Gas = Gas::from_tgas(5);
const RLP_EXECUTE_CALLBACK_GAS: Gas = Gas::from_tgas(5);
const ADDRESS_CHECK_CALLBACK_GAS: Gas = Gas::from_tgas(5).saturating_add(RLP_EXECUTE_CALLBACK_GAS);
const NEP_141_STORAGE_BALANCE_CALLBACK_GAS: Gas = Gas::from_tgas(5)
    .saturating_add(NEP_141_STORAGE_DEPOSIT_GAS)
    .saturating_add(RLP_EXECUTE_CALLBACK_GAS);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L140-159)
```rust
        self.has_in_flight_tx = false;
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
            },
        };
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L202-221)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L412-431)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs (L172-191)
```rust
/// A data type to keep track of the deposit given by an external caller.
/// This allows us to refund the caller's deposit if the cross-contract call fails.
#[derive(Debug, PartialEq, Eq, Clone, serde::Serialize, serde::Deserialize)]
pub struct CallerDeposit {
    pub account_id: AccountId,
    pub yocto_near: NonZeroU128,
}

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L170-213)
```rust
// An external caller gets its deposit back if the cross-contract call fails.
#[tokio::test]
async fn test_caller_refunds() -> anyhow::Result<()> {
    let TestContext { worker, wallet_contract, wallet_sk, address_registrar, .. } =
        TestContext::new().await?;

    let caller = worker.root_account()?;
    let deposit_amount = NearToken::from_near(3);
    let create_tx = |receiver_id: &AccountId, nonce: u64| {
        let method = "register";
        let args = br#"{"account_id": "birchmd.near"}"#;
        let action = Action::FunctionCall {
            receiver_id: receiver_id.to_string(),
            method_name: method.into(),
            args: args.to_vec(),
            gas: Gas::from_tgas(10).as_gas(),
            yocto_near: 0,
        };
        utils::create_signed_transaction(
            nonce,
            receiver_id,
            Wei::new_u128(deposit_amount.as_yoctonear() / (MAX_YOCTO_NEAR as u128)),
            action,
            &wallet_sk,
        )
    };

    // External caller gets a refund when the cross-contract call fails
    let pre_tx_account_balance = caller.view_account().await?.balance;
    let receiver_id: AccountId = "fake.near".parse()?;
    let result = wallet_contract
        .rlp_execute_from(
            &caller,
            receiver_id.as_str(),
            &create_tx(&receiver_id, 0),
            deposit_amount,
        )
        .await?;
    assert!(!result.success);
    let post_tx_account_balance = caller.view_account().await?.balance;
    assert!(
        pre_tx_account_balance.as_yoctonear() - post_tx_account_balance.as_yoctonear()
            < deposit_amount.as_yoctonear()
    );
```
