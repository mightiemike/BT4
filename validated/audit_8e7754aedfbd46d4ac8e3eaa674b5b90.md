### Title
Wallet Contract's `has_in_flight_tx` lock can become permanently stuck `true` if the resolving callback receipt fails, permanently freezing the account's ability to transact - ([File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs])

### Summary
The NEAR Wallet Contract (used to give ETH-implicit accounts NEAR-account-like transaction semantics) enforces "only one transaction in flight at a time" via a boolean flag `has_in_flight_tx`. This flag is only cleared inside privileged, asynchronously-scheduled callback methods (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`). If the receipt carrying one of these callbacks fails for any reason before it can execute the `self.has_in_flight_tx = false;` statement, the flag remains `true` forever, since a failed receipt's state changes (including the earlier commit of `has_in_flight_tx = true`) are never re-executed by anyone else. This is directly analogous to the reported bug class: a single point of failure in an asynchronous "unlock/settle" step with no pull-based or alternative recovery path leaves the account's funds functionally inaccessible.

### Finding Description
`rlp_execute` refuses to process any new transaction while `has_in_flight_tx` is `true`: [1](#0-0) 

The flag is set to `true` when `rlp_execute` (or an intermediate callback) dispatches a promise, and is only ever reset to `false` at the very start of the callback that resolves that promise chain: [2](#0-1) [3](#0-2) [4](#0-3) 

Each callback (`rlp_execute_callback`, `nep_141_storage_balance_callback`, `address_check_callback`, `ban_relayer`) is dispatched as a *separate* action receipt with a statically fixed gas budget (`RLP_EXECUTE_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`, `ADDRESS_CHECK_CALLBACK_GAS`), e.g.: [5](#0-4) [6](#0-5) 

Per nearcore's receipt-execution model, an action receipt executes its actions atomically: if the `FunctionCall` action carrying the callback fails (e.g., insufficient prepaid/attached gas to even enter the function body, a WASM trap, or any other execution error), the entire receipt's state changes are rolled back and no other party can re-run it: [7](#0-6) [8](#0-7) 

Crucially, this rollback applies only to *that* receipt's own writes; it cannot retroactively undo the *earlier, already-committed* receipt that set `has_in_flight_tx = true` when it successfully spawned the promise chain (see `rlp_execute` lines 116-128 above, which commits `true` before returning `PromiseOrValue::Promise`). If the follow-up callback receipt (the only code path that ever clears the flag) subsequently fails outright, `has_in_flight_tx` is permanently stuck at `true`, and every future call to `rlp_execute` is unconditionally rejected with `"Error: transaction already in progress, please try again later."` (line 97-105). There is no admin/pull/recovery function exposed to reset this flag.

This mirrors the reported bug class precisely: an unrecoverable off-chain-triggerable failure of a single "unlocking" step (there: the oracle's `claimBounty()` call; here: the wallet contract's resolving callback) with no fallback pull mechanism leaves user funds permanently unreachable through their intended interface.

### Impact Explanation
Because `rlp_execute` is the sole entry point through which the eth-implicit account holder can move funds or interact with contracts via the wallet contract, a stuck `has_in_flight_tx` flag permanently locks the account out of using this interface. Any NEAR or NEP-141 tokens held by (or routable only through) this wallet-contract-controlled account become functionally frozen — they cannot be transferred, and no NEP-366-meta-tx or self-signed transaction can unstick the flag, since only the (now permanently unreachable) callback resets it. This is a permanently-frozen-funds condition reachable by any relayer/caller who can cause (deliberately or accidentally) the resolving callback receipt to fail, e.g. by supplying a signed transaction whose downstream cross-contract call consumes gas in a way that starves the fixed callback gas budget, or by a callee panicking unexpectedly.

### Likelihood Explanation
The trigger requires only a single relayer-submitted transaction (`rlp_execute`) whose subsequent promise chain resolves into a failing callback receipt. Given the callback's gas budgets are statically fixed constants unrelated to arbitrary receiver behavior (e.g. NEP-141 token contracts can consume variable amounts of gas, and a malicious/buggy/paused token contract callee can cause the second-stage promise or its callback to fail), an attacker or even ordinary operational failure (a paused/failing NEP-141 token, a registrar failure, or a gas miscalculation) can realistically cause this. No privileged access is required — a single crafted or unlucky transaction from an unprivileged relayer suffices.

### Recommendation
Do not gate all future usage on an in-flight promise resolving successfully. Options: (1) make the "single transaction in flight" guarantee resilient to callback failure, e.g., by having the *dispatching* code register a timeout/expiry after which `has_in_flight_tx` can be forcibly cleared, or (2) add a permissionless recovery/reset method that can restore `has_in_flight_tx` to `false` once the associated promise result (success or failure) is externally observable/verifiable, or (3) avoid using this single global flag/mutex pattern altogether in favor of a nonce-scoped in-flight marker so a failure in one transaction's callback chain cannot permanently block all future ones.

### Proof of Concept
1. A relayer submits an RLP-encoded ERC-20 transfer transaction via `rlp_execute` targeting an unregistered `receiver_id` on some NEP-141 token contract; `has_in_flight_tx` is set to `true` and a promise chain `storage_balance_of -> nep_141_storage_balance_callback -> [storage_deposit, ft_transfer] -> rlp_execute_callback` is spawned (`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs:433-458`, `239-269`).
2. Ensure the terminal `rlp_execute_callback` receipt fails outright before it can run `self.has_in_flight_tx = false;` (line 280) — for example by causing the preceding `ft_transfer` promise or its scheduling to consume more gas than the statically budgeted `RLP_EXECUTE_CALLBACK_GAS`, or by having the NEP-141 contract panic in a way that exhausts the callback's fixed gas envelope before the function body starts.
3. The failing callback receipt's state changes are rolled back per nearcore's atomic-receipt semantics (`runtime/runtime/AGENTS.md:36-38`), so `has_in_flight_tx` remains permanently `true` from the earlier, already-committed `rlp_execute` (or `nep_141_storage_balance_callback`) invocation.
4. Any subsequent call to `rlp_execute` (from any relayer, with any valid signed transaction) is rejected with `"Error: transaction already in progress, please try again later."` (`lib.rs:97-105`) indefinitely, permanently freezing the account's ability to transact through the wallet contract.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L36-41)
```rust
const REGISTRAR_LOOKUP_GAS: Gas = Gas::from_tgas(5);
const RLP_EXECUTE_CALLBACK_GAS: Gas = Gas::from_tgas(5);
const ADDRESS_CHECK_CALLBACK_GAS: Gas = Gas::from_tgas(5).saturating_add(RLP_EXECUTE_CALLBACK_GAS);
const NEP_141_STORAGE_BALANCE_CALLBACK_GAS: Gas = Gas::from_tgas(5)
    .saturating_add(NEP_141_STORAGE_DEPOSIT_GAS)
    .saturating_add(RLP_EXECUTE_CALLBACK_GAS);
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L94-105)
```rust
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L116-128)
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
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-202)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L275-281)
```rust
    #[private]
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L439-458)
```rust
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

**File:** runtime/runtime/AGENTS.md (L34-38)
```markdown
There are two main types of receipts: Action and Data receipts.

Action receipts contain a list of actions to execute. They arrive on the `receiver_id`'s account and execute actions from the list, one after the other.
If any action fails, the whole receipt fails, its execution outcome is Err.
There are many types of actions. The most interesting one is `FunctionCall` action, which allows to call a method on a smart contract in WASM.
```

**File:** protocol-model/spec/runtime-execution.md (L67-70)
```markdown
4. **Execute actions in order** (`runtime/runtime/src/lib.rs:848`): for each action compute an `action_hash`, call `apply_action`, and on success validate every newly created receipt with `validate_receipt(..., NewReceipt)` (`:871`). `merge` folds the result; on the first `Err` the loop records the action index and breaks (`runtime/runtime/src/lib.rs:884`).
5. If the receipt still succeeded, re-check receiver storage staking; a shortfall sets `LackBalanceForState` (`runtime/runtime/src/lib.rs:891`-`912`).
6. **Refunds** (see below): system-predecessor receipts (refund receipts) are free — no refund generated, and a failed refund burns its deposit into `other_burnt_amount` (`runtime/runtime/src/lib.rs:929`). Otherwise `refund_unspent_gas_and_deposits` runs (`:943`).
7. **Commit or rollback**: success commits with `ReceiptProcessing`; failure calls `state_update.rollback()`, discarding all state changes from the receipt (`runtime/runtime/src/lib.rs:961`-`970`).
```
