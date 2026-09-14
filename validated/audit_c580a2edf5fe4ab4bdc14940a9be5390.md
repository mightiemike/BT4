### Title
Attached deposit permanently locked in wallet contract when multi-step Eth-emulated transaction fails before the final callback - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
`WalletContract::rlp_execute` is a `#[payable]` entry point that accepts an attached NEAR deposit from an external caller (a relayer) and creates a `CallerDeposit` record specifically so that deposit can be refunded if the resulting cross-contract call fails. However, that refund logic is implemented only in the terminal `rlp_execute_callback`. The intermediate callbacks in the multi-step promise chains (`address_check_callback`, `nep_141_storage_balance_callback`) have failure branches that return an error `ExecuteResponse` directly without ever forwarding the `caller_deposit` for a refund, so the attached NEAR is permanently retained by the wallet contract — the exact "value sent to the contract gets stuck" bug class described in the report.

### Finding Description
`CallerDeposit::new` is explicitly documented as existing "to refund the caller's deposit if the cross-contract call fails" [1](#0-0) . It is created in `inner_rlp_execute` from the deposit attached to the payable `rlp_execute` call, and threaded through as a parameter into whichever callback finalizes the multi-step Ethereum-transaction emulation [2](#0-1) .

Only `rlp_execute_callback` implements the promised refund, and only for `PromiseResult::Failed`: [3](#0-2) 

But for transactions that require an address-registrar lookup (`EOABaseTokenTransfer` with `address_check`) or an NEP-141 storage-balance check (`ERC20Transfer`), the flow first goes through `address_check_callback` or `nep_141_storage_balance_callback`, which also receive `caller_deposit` as a parameter [4](#0-3) [5](#0-4) . Their early-return failure branches drop `caller_deposit` on the floor instead of refunding it:

- Registrar call fails: [6](#0-5) 
- Target resolves to an existing named account (invalid-target error): [7](#0-6) 
- `storage_balance_of` call to the token contract fails: [8](#0-7) 

In every one of these branches the function returns `PromiseOrValue::Value(ExecuteResponse{ success:false, ... })` immediately — no promise is created to send `caller_deposit.yocto_near` back to `caller_deposit.account_id`. Because the deposit was already credited to the wallet contract's own account balance when the payable `rlp_execute` transaction executed, and no compensating transfer is ever issued, the funds stay in the wallet contract permanently, unreachable by the caller who sent them (mirroring the Timelock case where funds sent to the contract are not correctly routed back out).

### Impact Explanation
Any relayer (an unprivileged caller reachable via a normal NEAR transaction/RPC call) that attaches a deposit intended to fund a `FunctionCall` or `Transfer` action inside an Ethereum-emulated transaction can have that deposit permanently frozen in the wallet contract whenever:
- the address-registrar lookup call fails (e.g., due to gas limits, congestion, or if the registrar account is unavailable), or
- the target turns out to be an existing named account (a legitimate, easily triggerable condition), or
- an NEP-141 token's `storage_balance_of` view call fails (e.g., malicious/broken token contract, or gas exhaustion).

This is a concrete "permanently frozen funds" condition matching the required impact class: value sent by a caller becomes unrecoverable, with no code path to retrieve it afterward.

### Likelihood Explanation
The condition is reachable by ordinary use of the public, payable `rlp_execute` method by any relayer or user, with no special privileges needed. Failure of an external cross-contract call (registrar lookup or `storage_balance_of`) is a normal occurrence (gas limits, unavailable/misbehaving contracts, or legitimate "target is a named account" responses), making this readily triggerable, not merely a theoretical edge case.

### Recommendation
In `address_check_callback` and `nep_141_storage_balance_callback`, every failure branch that currently returns `PromiseOrValue::Value(...)` directly should instead, when `caller_deposit` is `Some`, first create a promise batch and issue `promise_batch_action_transfer` refunding `yocto_near` to `account_id` (as already done in `rlp_execute_callback`) before/while returning the error response, e.g. by refactoring the refund logic in `rlp_execute_callback` (lines 296-305) into a shared helper and invoking it from all early-failure return sites in the two intermediate callbacks.

### Proof of Concept
1. Deploy the wallet contract to an eth-implicit account and fund a relayer account.
2. Have the relayer call the payable `rlp_execute(target, tx_bytes_b64)` with an attached deposit, submitting an RLP-encoded Ethereum transaction that decodes to an `EOABaseTokenTransfer` with `address_check: Some(address)` (i.e., targeting another eth-implicit account), per `inner_rlp_execute`'s dispatch at lines 412-432.
3. This creates a promise to the address-registrar contract followed by `address_check_callback`, passing along `caller_deposit` (the attached NEAR) — see lines 419-431.
4. Cause the registrar lookup promise to fail (e.g., attach insufficient gas, or have the registrar contract panic/be unavailable).
5. `address_check_callback` hits the `PromiseResult::Failed` branch at lines 142-148 and returns an error response without ever issuing a refund transfer for `caller_deposit`.
6. Verify on-chain that the relayer's attached deposit remains in the wallet contract's balance and is never returned to the relayer — the deposit is now permanently locked, exactly as in the "Eth sent to Timelock" scenario in the reference report.

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L133-139)
```rust
    #[private]
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L141-148)
```rust
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Call to Address Registrar contract failed".into()),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L161-173)
```rust
        let promise = if maybe_account_id.is_some() {
            // We intentionally do not increment the nonce in this case because the
            // error is caused by a faulty relayer, not the user. An honest relayer
            // may still be able to successfully send the user's intended transaction.
            if env::signer_account_id() == current_account_id {
                create_ban_relayer_promise(current_account_id)
            } else {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some("Invalid target: target is address corresponding to existing named account_id".into()),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-201)
```rust
    #[private]
    pub fn nep_141_storage_balance_callback(
        &mut self,
        token_id: AccountId,
        receiver_id: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L203-210)
```rust
        let maybe_storage_balance: Option<StorageBalance> = match env::promise_result(0) {
            PromiseResult::Failed => {
                return PromiseOrValue::Value(ExecuteResponse {
                    success: false,
                    success_value: None,
                    error: Some(format!("Call to NEP-141 {token_id}::storage_balance_of failed")),
                });
            }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-311)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-345)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);
```
