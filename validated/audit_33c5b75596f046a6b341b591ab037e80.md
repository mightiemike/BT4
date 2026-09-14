## Title
Attached NEAR deposit is permanently stranded in the Wallet Contract when `rlp_execute` rejects a transaction with a `UserError` - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
`WalletContract::rlp_execute` is a `#[payable]` entry point that any unprivileged caller (or relayer) can invoke while attaching a NEAR deposit. When the RLP-encoded Ethereum transaction it decodes triggers a `UserError` (e.g. `ExcessYoctoNear`, `UnknownFunctionSelector`, `InvalidAbiEncodedData`, `UnsupportedAction`, etc.), `inner_rlp_execute` returns `Err(Error::User(_))` before any promise is scheduled, and `rlp_execute` converts that error directly into a successful `ExecuteResponse` value with no promise created to return the caller's attached deposit.

### Finding Description
`rlp_execute` is marked `#[payable]` and reads the attached deposit into an `ExecutionContext`: [1](#0-0) 

Inside `inner_rlp_execute`, a `CallerDeposit` is computed from the context (this is the value later used, in other code paths, to refund the caller if a cross-contract call subsequently fails): [2](#0-1) 

However, when `parse_rlp_tx_to_action` fails with a `UserError` (malformed/invalid ABI-encoded action data, unsupported action types, excess yocto-NEAR encoding, unknown function selector, etc.), the function increments the nonce and returns immediately — `caller_deposit` is discarded and never used to build a refund promise: [3](#0-2) 

Back in `rlp_execute`, this `Err(Error::User(_))` falls through the match arm that just converts the error to a plain `ExecuteResponse` value, with **no promise at all**: [4](#0-3) 

Because `rlp_execute` returns a normal successful value (not a panic), NEAR's protocol-level automatic deposit refund (which only fires when the *entire receipt* fails/panics, per `refund_unspent_gas_and_deposits`) does not trigger: [5](#0-4) [6](#0-5) 

The attached deposit was already credited to the Wallet Contract account's balance before the contract logic even ran (`account_balance` includes `attached_deposit` per the Economics API), so once the function returns successfully without spawning a refund promise, the deposit simply becomes part of the Wallet Contract's own balance with no code path that returns it to the caller: [7](#0-6) 

This mirrors the reported Solidity bug class exactly: a `payable`-style entry point accepts value, but a specific rejection branch has no refund/withdraw logic, and the received value is permanently absorbed by the contract.

The `UserError` variants are explicitly documented as reachable by ordinary users (not just misbehaving relayers) whenever the front-end incorrectly constructs the signed Ethereum transaction data (e.g. `ExcessYoctoNear`, `InvalidAbiEncodedData`, `UnknownFunctionSelector`, `UnsupportedAction`): [8](#0-7) 

By contrast, other failure paths in the same contract *do* build an explicit refund promise for the caller when a cross-contract call fails after being dispatched, showing the developers were aware of the refund requirement but missed this early-rejection branch: [9](#0-8) 

### Impact Explanation
Any unprivileged caller who attaches a NEAR deposit to `rlp_execute` and supplies an Ethereum-encoded transaction that fails ABI/action decoding with a `UserError` loses that deposit permanently — it becomes indistinguishable native-token balance of the Wallet Contract account, with no exposed method to withdraw or refund it. This is a concrete, unauthorized/unintended value loss reachable by a single external call, matching the "permanently frozen/lost funds" acceptance criterion. Given that `rlp_execute` is the primary entry point end users and relayers are expected to call with deposits attached (e.g., for value transfers or fee payments encoded as Ethereum transactions), realistic malformed-encoding scenarios (front-end bugs, excess yocto-NEAR remainder, unsupported action encodings) can trigger this loss.

### Likelihood Explanation
Medium likelihood: this requires the caller to attach a deposit and submit a transaction whose ABI-encoded payload fails one of the specific `UserError` checks (as opposed to a `RelayerError`, which is a different code path). This is plausible from buggy or malicious front-end/relayer software constructing the signed Ethereum transaction, or a user directly crafting the call. No special privilege is needed — only an ordinary account able to call `rlp_execute` with a deposit.

### Recommendation
In `inner_rlp_execute`, when returning `Err(Error::User(_))` (and any other early-return error path after `caller_deposit` has been computed but before a promise is dispatched), spawn a promise that transfers the attached deposit back to `predecessor_account_id` before returning the error, mirroring the refund logic already implemented in `rlp_execute_callback` for failed cross-contract calls. Alternatively, restructure `rlp_execute` so that any `Err` path with a non-zero `caller_deposit` always produces a refund promise rather than a plain `PromiseOrValue::Value`.

### Proof of Concept
1. Deploy the Wallet Contract to an eth-implicit account as in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs`.
2. As an external caller, call `rlp_execute` with `target` set to some valid receiver, attaching a non-zero NEAR deposit (e.g. via `rlp_execute_from`, as used in `test_caller_refunds`), and with `tx_bytes_b64` encoding an Ethereum transaction whose ABI-encoded action data triggers `UserError::InvalidAbiEncodedData` or `UserError::ExcessYoctoNear` (e.g. malformed `Action::FunctionCall` bytes, or a `Transfer`/`FunctionCall` yocto-NEAR remainder exceeding `MAX_YOCTO_NEAR`).
3. Observe: `result.success == false` (a `UserError` is returned in `ExecuteResponse.error`), yet the caller's account balance decreases by the attached deposit amount and the Wallet Contract's balance increases by the same amount — unlike the assertions in `test_caller_refunds` (lines 197–213) which currently only cover the "cross-contract call fails after dispatch" refund path, not the `UserError` early-rejection path.
4. No subsequent call exists that lets the caller reclaim this stranded deposit from the Wallet Contract.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-114)
```rust
    #[payable]
    pub fn rlp_execute(
        &mut self,
        target: AccountId,
        tx_bytes_b64: String,
    ) -> PromiseOrValue<ExecuteResponse> {
        // To ensure user actions are executed in the desired order,
        // having multiple transactions in flight at the same time is
        // not allowed.
        if self.has_in_flight_tx {
            return PromiseOrValue::Value(ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(
                    "Error: transaction already in progress, please try again later.".into(),
                ),
            });
        }
        let current_account_id = env::current_account_id();
        let predecessor_account_id = env::predecessor_account_id();
        let result = inner_rlp_execute(
            current_account_id.clone(),
            predecessor_account_id,
            target,
            tx_bytes_b64,
            &mut self.nonce,
        );
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L116-127)
```rust
        match result {
            Ok(promise) => {
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(Error::Relayer(_)) if env::signer_account_id() == current_account_id => {
                let promise = create_ban_relayer_promise(current_account_id);
                self.has_in_flight_tx = true;
                PromiseOrValue::Promise(promise)
            }
            Err(e) => PromiseOrValue::Value(e.into()),
        }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L296-306)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L340-346)
```rust
    let context = ExecutionContext::new(
        current_account_id.clone(),
        predecessor_account_id,
        env::attached_deposit(),
    )?;
    let caller_deposit = CallerDeposit::new(&context);

```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L389-393)
```rust
        Err(err @ Error::User(_)) => {
            // Increment nonce on all user errors to prevent replay.
            *nonce = nonce.saturating_add(1);
            return Err(err);
        }
```

**File:** runtime/runtime/src/lib.rs (L1284-1307)
```rust
    fn refund_unspent_gas_and_deposits(
        &self,
        gas_burn_price: Balance,
        gas_purchase_price: Balance,
        receipt: &Receipt,
        action_receipt: &VersionedActionReceipt,
        result: &mut ActionReceiptResult,
        config: &RuntimeConfig,
        created_account: bool,
        protocol_version: ProtocolVersion,
    ) -> Result<GasRefundResult, RuntimeError> {
        let total_deposit = total_deposit(&action_receipt.actions())?;
        let prepaid_gas = total_prepaid_gas(&action_receipt.actions())?
            .checked_add(total_prepaid_send_fees(config, &action_receipt.actions())?.gas)
            .ok_or(IntegerOverflowError)?;
        let prepaid_exec_gas =
            total_prepaid_exec_fees(config, &action_receipt.actions(), receipt.receiver_id())?
                .checked_add(config.fees.fee(ActionCosts::new_action_receipt).exec_fee())
                .ok_or(IntegerOverflowError)?;
        let deposit_refund = if result.result.is_err() { total_deposit } else { Balance::ZERO };
        let gross_gas_refund = if result.result.is_err() {
            prepaid_gas
                .checked_add(prepaid_exec_gas.gas)
                .ok_or(IntegerOverflowError)?
```

**File:** docs/RuntimeSpec/Refunds.md (L15-18)
```markdown
## Deposit Refunds

Deposit refunds are generated when an action receipt fails to execute. All attached deposit amounts are summed together and
sent as a refund to a `predecessor_id` (because only the predecessor can attach deposits).
```

**File:** docs/RuntimeSpec/Components/BindingsSpec/EconomicsAPI.md (L7-15)
```markdown
- `account_balance` -- the balance attached to the given account. This includes the `attached_deposit` that was attached
  to the transaction;
- `attached_deposit` -- the balance that was attached to the call that will be immediately deposited before
  the contract execution starts;
- `prepaid_gas` -- the tokens attached to the call that can be used to pay for the gas;
- `used_gas` -- the gas that was already burnt during the contract execution and attached to promises (cannot exceed `prepaid_gas`);

If contract execution fails `prepaid_gas - used_gas` is refunded back to `signer_account_id` and `attached_deposit`
is refunded back to `predecessor_account_id`.
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/error.rs (L45-63)
```rust
/// Errors that arise from problems in the data signed by the user
/// (i.e. in the Ethereum transaction itself). A careful power-user
/// should never see these errors because they can review the data
/// they are signing. If a user does see these errors then there is
/// likely a bug in the front-end code that is constructing the Ethereum
/// transaction to be signed.
#[derive(Debug, PartialEq, Eq, Clone)]
pub enum UserError {
    EvmDeployDisallowed,
    ValueTooLarge,
    UnknownPublicKeyKind,
    InvalidEd25519Key,
    InvalidSecp256k1Key,
    InvalidAccessKeyAccountId,
    UnsupportedAction(UnsupportedAction),
    UnknownFunctionSelector,
    InvalidAbiEncodedData,
    ExcessYoctoNear,
}
```
