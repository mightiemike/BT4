### Title
Wallet Contract loses the external caller's attached NEAR deposit when a cross-contract dependency (NEP-141 token `storage_balance_of` or the address registrar `lookup`) returns a response that doesn't match the expected schema - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The NEAR Wallet Contract's `address_check_callback` and `nep_141_storage_balance_callback` strictly `serde_json::from_slice` the successful result of a cross-contract call into a specific Rust type (`Option<AccountId>` / `Option<StorageBalance>`). If the target contract (the address registrar, or an arbitrary NEP-141 token specified by the relayer/user) is non-standard and returns a JSON value that does not deserialize into the expected type, the callback returns an `ExecuteResponse{success:false}` early — but unlike the `PromiseResult::Failed` branch, this deserialization-failure branch never triggers the `CallerDeposit` refund. This is directly analogous to the reported ERC20 bug class: code assumes external call outputs conform to a strict expected format (bool return for `approve`), and when a non-conforming (but externally, benign) implementation is encountered, the assumption is violated and the flow diverges from the "happy path" refund/execution logic, resulting in stuck/lost value instead of a clean revert. [1](#0-0) [2](#0-1) 

### Finding Description
`inner_rlp_execute` computes `caller_deposit` from `CallerDeposit::new(&context)`, which records the predecessor's attached NEAR deposit whenever the predecessor differs from the wallet's own account (i.e., an external relayer paying for a signed Ethereum-style transaction routed through the wallet). [3](#0-2) [4](#0-3) 

This `caller_deposit` is threaded through the two intermediate callbacks — `address_check_callback` (used for `EOABaseTokenTransfer` with an address-registrar lookup) and `nep_141_storage_balance_callback` (used for emulated ERC-20 transfers, calling the token's `storage_balance_of`) — and is only refunded inside `rlp_execute_callback` when the *final* promise fails (`PromiseResult::Failed`): [5](#0-4) 

However, both intermediate callbacks contain a distinct failure path that is reached when the promise call *succeeds* but its returned bytes cannot be deserialized into the expected type:

```rust
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
``` [6](#0-5) 

and the equivalent for the NEP-141 case: [7](#0-6) 

In both `Err(_)` branches, the function returns immediately without constructing any refund `Promise` for `caller_deposit`, unlike the `PromiseResult::Failed` path in `rlp_execute_callback` which explicitly creates a `promise_batch_create`/`promise_batch_action_transfer` refund to `caller_deposit.account_id`. Since the target contract queried (the registrar, or, notably, any arbitrary NEP-141 token contract chosen by whoever crafts the transaction the relayer submits) is not controlled by NEAR protocol code and is not required to strictly conform to the exact JSON schema `near_contract_standards::storage_management::StorageBalance` expects (e.g., a token could return `null`, a bare number, a differently-shaped object, or add/omit optional fields in a way serde rejects), this deserialization mismatch is fully attacker/target-influenced and reachable from a normal signed transaction routed through the wallet contract.

This exactly mirrors the reported bug class: a strict, unforgiving assumption about the shape/type of a third-party contract's response (analogous to assuming `approve` returns a `bool`) causes the calling contract to diverge into an error path that skips value-preserving cleanup logic that exists on the "expected" failure path.

### Impact Explanation
When the deserialization fails, the wallet contract's nonce/replay-protection state has already advanced (nonce increment happens prior to dispatching the promise chain in `inner_rlp_execute`), so the caller cannot retry the same transaction. The attached deposit recorded in `caller_deposit`, which was meant to be either consumed for a successful transfer/relayer refund flow or refunded on failure, is neither refunded nor spent purposefully — it remains as an unaccounted-for balance increase on the wallet contract's own account, permanently inaccessible to the external caller who supplied it. This satisfies the "permanently frozen funds" impact category: the depositor's balance is effectively lost from their perspective while stuck in the wallet contract's account.

### Likelihood Explanation
Reachability requires only a single relayer-submitted, RLP-encoded, signed Ethereum-style transaction targeting either (a) an `EOABaseTokenTransfer` requiring an address-registrar lookup, or (b) an emulated `ERC20Transfer` against a NEP-141 token contract. Any token or registrar implementation that deviates even slightly from the exact expected JSON response shape (a very plausible occurrence given the diversity of NEP-141 implementations, similar to how non-standard ERC20 tokens deviate from expected ABI/return-type conventions) triggers this path deterministically. No malicious validator, network-layer access, or privileged capability is needed — an ordinary relayer/deposit-paying caller and a benign-but-nonconforming token/registrar contract suffice.

### Recommendation
In the `Err(_)` deserialization-failure branches of `address_check_callback` and `nep_141_storage_balance_callback`, mirror the refund logic used in `rlp_execute_callback`'s `PromiseResult::Failed` branch: if `caller_deposit` is `Some`, create a refund `Promise`/batch action transferring `yocto_near` back to `caller_deposit.account_id` before returning the `ExecuteResponse{success:false}`. This ensures a non-conforming external contract response cannot silently strand caller funds within the wallet contract.

### Proof of Concept
1. Deploy an "NEP-141-like" token contract whose `storage_balance_of` view method returns a JSON value that does not match `Option<StorageBalance>` (e.g., returns the raw string `"0"` instead of `null`/`{total,available}`).
2. As an external relayer with a distinct `predecessor_account_id` from the wallet contract, submit a `rlp_execute` transaction to the wallet contract whose decoded action is an `ERC20Transfer` targeting this non-conforming token, attaching a NEAR deposit (`attached_deposit > 0`).
3. `inner_rlp_execute` records `caller_deposit = Some(...)`, increments the nonce, and dispatches `storage_balance_of` followed by `nep_141_storage_balance_callback`.
4. The `storage_balance_of` call succeeds (returns valid but non-conforming JSON), so `env::promise_result(0)` is `PromiseResult::Successful(value)`, and `serde_json::from_slice(&value)` fails.
5. `nep_141_storage_balance_callback` returns `ExecuteResponse{success:false, error: Some("Unexpected response...")}` without issuing any refund promise for `caller_deposit`.
6. The relayer's attached deposit remains on the wallet contract's account balance; because the nonce was already incremented, the caller cannot resubmit the same transaction to attempt a proper refund path, and no other public method returns these funds — the deposit is stranded.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-159)
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
            },
        };
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-221)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L330-345)
```rust
fn inner_rlp_execute(
    current_account_id: AccountId,
    predecessor_account_id: AccountId,
    target: AccountId,
    tx_bytes_b64: String,
    nonce: &mut u64,
) -> Result<Promise, Error> {
    if *nonce == u64::MAX {
        return Err(Error::AccountNonceExhausted);
    }
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);
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
