## Finding

### Title
Wallet-Contract `has_in_flight_tx` flag can be left permanently stuck by a failed callback, freezing the ETH-implicit account forever - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (the contract deployed on every ETH-implicit account to let a relayer submit Ethereum-style transactions on behalf of the owner) uses a boolean guard, `has_in_flight_tx`, to prevent more than one transaction from being in flight at a time. `rlp_execute` immediately rejects any call while the flag is `true` [1](#0-0) , and the flag is only cleared inside downstream, separately-scheduled callback receipts (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`) [2](#0-1) [3](#0-2) [4](#0-3) .

### Finding Description
This is structurally the same bug class as the FrankenDAO issue: a per-account "lock" flag is set by one transaction and can only be released by a *subsequent, separate* action outside the direct control of the account owner, and there is no alternate exit path once that release action fails to happen.

- `rlp_execute` sets `self.has_in_flight_tx = true` before returning a `Promise` chain, and *only* a later callback receipt can reset it to `false` [5](#0-4) .
- Every callback resets the flag as its very first statement (e.g. `self.has_in_flight_tx = false;` at the top of `rlp_execute_callback`) [3](#0-2) . NEAR contract execution is atomic per function call: any panic or out-of-gas failure occurring later in that same callback discards **all** state writes performed during that call, including this early reset. The callback's gas is a small fixed budget (`RLP_EXECUTE_CALLBACK_GAS = 5 Tgas`, or `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`, etc.) reserved at promise-creation time by whoever submits the outer transaction [6](#0-5) , and callback logic still performs extra work in some branches (e.g. constructing a refund `Promise` in the `Failed` branch of `rlp_execute_callback`) [7](#0-6) .
- Once `has_in_flight_tx` is stuck `true`, every future `rlp_execute` call short-circuits to an error without ever creating a new promise that could reset it [8](#0-7) .
- Unlike a normal NEAR account, an ETH-implicit account "cannot be deleted, nor can a full access key be added" [9](#0-8) , and `rlp_execute` is documented as "the main entry point into this contract" [10](#0-9) . There is no admin/emergency-eject function analogous to the fix recommended in the FrankenDAO report.

Just as the FrankenDAO delegate could keep re-opening proposals to keep the `lockedWhileVotesCast` condition permanently true and trap the delegatee, here the relayer role (an unprivileged party paying gas for the account owner, filling the same role as a meta-transaction sender/RPC caller) controls the exact gas budget of the outer `rlp_execute` call and therefore the exact gas left over for the scheduled callback. Because there is no fallback/timeout/cooldown mechanism to clear `has_in_flight_tx` if a callback receipt fails, a callback that fails to run to completion (whether by malicious gas-starvation from the relayer or an unanticipated code path) leaves the flag permanently set, and the account becomes unusable through `rlp_execute` forever.

### Impact Explanation
If `has_in_flight_tx` is stuck `true`, the account's entire balance (and any NEP-141/other token balances reachable only through actions signed for that account) become permanently frozen: no further `Transfer`, `FunctionCall`, `AddKey`, or `DeleteKey` action can ever be executed for that account, since `rlp_execute` is the only way to act as the account and it is now permanently gated. This matches the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
The relayer submitting the outer transaction fully controls the gas attached to it (an unprivileged, un-trusted role by design — the whole point of the wallet contract is to let arbitrary relayers pay gas for the owner). Because the callback's gas reservation is fixed by the contract at promise-scheduling time and any extra work performed inside that callback (JSON/borsh handling of `PromiseResult`, constructing refund promises in `Failed`/`None` branches) is not proven bounded well below that reservation in every version and under adversarial inputs, an adversarial or careless relayer choosing borderline-insufficient gas can push a callback receipt into an out-of-gas panic after the flag-reset statement has already been recorded in-memory but before the function returns, causing the atomic rollback described above.

### Recommendation
- Make the `has_in_flight_tx` reset resilient to callback failure, e.g. by never letting the reset happen only as the first line of a function whose later statements can panic/OOG; instead split "reset flag" and "compute result" into separately committed effects, or use a scheduled self-callback with `PromiseResult::Failed` explicitly handled to also clear the flag with guaranteed-sufficient static gas.
- Reserve significantly more static gas than currently required for every callback branch, with margin validated against worst-case inputs (large `success_value`, refund promise creation, etc.).
- Add an emergency/timeout mechanism (analogous to the FrankenDAO fix's "emergency eject" idea) that allows the account to recover from a stuck `has_in_flight_tx` state after some cooldown, instead of relying purely on callback completion.

### Proof of Concept
1. Owner signs a valid RLP Ethereum transaction for their ETH-implicit account (e.g. a simple base-token `Transfer`).
2. A relayer submits it via `rlp_execute`, attaching just enough gas for the outer call and promise scheduling to succeed but leaving the reserved callback gas (`RLP_EXECUTE_CALLBACK_GAS`/`NEP_141_STORAGE_BALANCE_CALLBACK_GAS`) insufficient for the callback branch that actually executes (e.g. the `Failed` branch of `rlp_execute_callback` which creates an additional refund `Promise`) [7](#0-6) .
3. `rlp_execute` runs, sets `has_in_flight_tx = true`, and returns the scheduled promise chain successfully [11](#0-10) .
4. The callback receipt executes later; it sets `has_in_flight_tx = false` in memory as its first line, then panics/runs out of gas while creating the refund promise. Because the whole function's state effects are atomic, the reset is discarded, leaving `has_in_flight_tx = true` in committed state.
5. All subsequent calls to `rlp_execute`, regardless of who submits them or what nonce/signature is used, immediately return "transaction already in progress" [8](#0-7) , and since no full-access key can ever be added and the account cannot be deleted, the account's funds are permanently inaccessible.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L71-77)
```rust
    /// This is the main entry point into this contract. It accepts an RLP-encoded
    /// Ethereum transaction signed by the private key associated with the address
    /// for the account where this contract is deployed. RLP is a binary format,
    /// so the argument is actually passed as a base64-encoded string.
    /// The Ethereum transaction represents a Near action the owner of the address
    /// wants to perform. This method decodes that action from the Ethereum transaction
    /// and crates a promise to perform that action.
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L93-105)
```rust
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L106-128)
```rust
        let current_account_id = env::current_account_id();
        let predecessor_account_id = env::predecessor_account_id();
        let result = inner_rlp_execute(
            current_account_id.clone(),
            predecessor_account_id,
            target,
            tx_bytes_b64,
            &mut self.nonce,
        );

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L134-140)
```rust
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L276-281)
```rust
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L319-327)
```rust
    #[private]
    pub fn ban_relayer(&mut self) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        ExecuteResponse {
            success: false,
            success_value: None,
            error: Some("Error: faulty relayer".into()),
        }
    }
```

**File:** docs/DataStructures/Account.md (L121-122)
```markdown
An ETH-implicit account can only be used by calling the methods of the [Wallet Contract](#wallet-contract). It cannot be deleted, nor can a full access key be added.
The primary purpose of ETH-implicit accounts is to enable seamless integration of existing Ethereum tools (such as wallets) with the NEAR blockchain.
```
