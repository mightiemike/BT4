### Title
Wallet Contract In-Flight Transaction Lock Has No Cancellation/Timeout Mechanism, Permanently Freezing the Wallet - (File: runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs)

### Summary
The NEAR Wallet Contract (`WalletContract`) enforces a single-in-flight-transaction invariant using the boolean field `has_in_flight_tx`, analogous to the Crestal `BlueprintCore` contract's single-pending-deployment-request invariant. `rlp_execute` refuses to start any new work while `has_in_flight_tx` is `true`, and the flag is only ever cleared inside the private promise-callback methods (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`). There is no timeout, admin override, or any other path to reset the flag if the scheduled callback chain never successfully executes.

### Finding Description
`rlp_execute` sets `self.has_in_flight_tx = true` as soon as it schedules a promise chain, and every subsequent call to `rlp_execute` short-circuits with an error while the flag remains `true`: [1](#0-0) 

The flag is reset to `false` only inside `#[private]` callback functions, each of which resets it as its very first statement before doing further work: [2](#0-1) [3](#0-2) [4](#0-3) 

Because NEAR contract-call state changes are atomic per receipt (a panicking or out-of-gas function call discards all writes performed by that call, including ones made before the failure point), if the scheduled callback receipt itself fails to execute successfully — e.g., it runs out of attached gas, or any host error occurs before it returns — the `has_in_flight_tx = false` write at the top of that same callback is rolled back along with everything else. Since these callback methods are `#[private]` (only invocable by the contract itself via a scheduled promise, not by an external signer), there is no way for an account owner or anyone else to directly call them to clear the flag. Unlike the runtime's `PromiseYield`/`PromiseResume` mechanism, which has a protocol-enforced `yield_timeout_length_in_blocks` timeout that always resolves a stuck yield: [5](#0-4) 

the Wallet Contract's `has_in_flight_tx` lock has no equivalent timeout, expiry, or cancellation function. This is the same root cause as the Crestal `BlueprintCore` finding: a single-pending-unit-of-work invariant enforced purely by a boolean/ID flag, with the only reset path depending on the "worker" (here, the scheduled callback receipt) completing successfully, and no fallback if it does not.

### Impact Explanation
If any promise in the chain scheduled by `rlp_execute` (target call → address/storage-balance lookup → `rlp_execute_callback`/`ban_relayer`) fails to complete its terminal callback successfully — most plausibly via gas exhaustion of the callback itself, which can be triggered by an under-gassed outer transaction submitted by any relayer (an unprivileged caller of the public, payable `rlp_execute` method) — `has_in_flight_tx` remains permanently `true`. From that point on, every subsequent `rlp_execute` call for this wallet is rejected with "transaction already in progress," for all future users and relayers, indefinitely. This permanently freezes the account's ability to process any Ethereum-style meta-transaction through the wallet contract, which is the sole intended way of transacting through this contract — effectively the "permanently frozen funds"/"transaction-triggered halt" class of impact.

### Likelihood Explanation
The lock can be triggered by any account able to call the public `rlp_execute` method (any relayer, not just the wallet owner or an access key holder), simply by attaching insufficient gas relative to what the resulting callback chain needs, or by any other transient failure in the callback receipt (e.g., a downstream contract call gas miscalculation for ERC20/`storage_deposit` flows). No privileged access, validator collusion, or network-layer manipulation is required — a single malformed or adversarial transaction is sufficient.

### Recommendation
Do not rely solely on a same-receipt boolean flag reset by the terminal callback. Add a way to recover from a stuck `has_in_flight_tx`, for example:
- Record the block height (or a similar deadline) at which the in-flight transaction was started, and allow `rlp_execute` to proceed (clearing the stale flag) if a sufficiently large number of blocks has elapsed without resolution, mirroring the `PromiseYield` timeout pattern in `runtime/runtime/src/lib.rs`.
- Alternatively/additionally, ensure sufficient static gas is always reserved for the terminal callback regardless of attacker-supplied `action.gas()`, and validate that `prepaid_gas` covers the full worst-case callback chain, not just the RLP transaction's own declared `gas_limit`.

### Proof of Concept
1. A relayer submits a `FunctionCall` to `rlp_execute` with a validly-signed RLP transaction whose action routes through the ERC-20 transfer path, requiring `storage_balance_of` lookup → `nep_141_storage_balance_callback` → possible `storage_deposit` + transfer → `rlp_execute_callback`.
2. `rlp_execute` succeeds locally, sets `has_in_flight_tx = true`, and schedules the promise chain (lines 90-127).
3. The relayer intentionally attaches just enough gas to pass the `validate_tx_relayer_data` gas check but insufficient gas for the full multi-hop callback chain to complete (line 362-365 in `internal.rs`, and gas math in `lib.rs` lines 39-41, 440-458).
4. The terminal callback receipt runs out of gas mid-execution; per NEAR's atomic-state-change semantics for function calls, the `has_in_flight_tx = false` write performed at the top of that callback (line 202 or 280) is rolled back with the rest of the failed receipt's state changes.
5. `has_in_flight_tx` remains `true` in contract state. All subsequent calls to `rlp_execute` by any account are rejected at lines 97-105 with "transaction already in progress," permanently, since no external caller can invoke the `#[private]` callbacks to reset the flag and there is no timeout mechanism.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L93-127)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L133-141)
```rust
    #[private]
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
        let maybe_account_id: Option<AccountId> = match env::promise_result(0) {
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L276-281)
```rust
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();
```

**File:** runtime/runtime/src/lib.rs (L3113-3148)
```rust
fn resolve_promise_yield_timeouts(
    processing_state: &mut ApplyProcessingReceiptState,
    receipt_sink: &mut ReceiptSink,
    compute_limit: u64,
) -> Result<ResolvePromiseYieldTimeoutsResult, RuntimeError> {
    let mut state_update = &mut processing_state.state_update;
    let total = &mut processing_state.total;
    let apply_state = &processing_state.apply_state;

    let mut promise_yield_indices: PromiseYieldIndices =
        get(state_update, &TrieKey::PromiseYieldIndices)?.unwrap_or_default();
    let initial_promise_yield_indices = promise_yield_indices.clone();
    let mut new_receipt_index: usize = 0;

    let mut processed_yield_timeouts = vec![];
    let yield_processing_start = std::time::Instant::now();
    while promise_yield_indices.first_index < promise_yield_indices.next_available_index {
        if total.compute >= compute_limit || state_update.trie.check_proof_size_limit_exceed() {
            break;
        }

        let queue_entry_key =
            TrieKey::PromiseYieldTimeout { index: promise_yield_indices.first_index };

        let queue_entry =
            get::<PromiseYieldTimeout>(state_update, &queue_entry_key)?.ok_or_else(|| {
                StorageError::StorageInconsistentState(format!(
                    "PromiseYield timeout queue entry #{} should be in the state",
                    promise_yield_indices.first_index
                ))
            })?;

        // Queue entries are ordered by expires_at
        if queue_entry.expires_at > apply_state.block_height {
            break;
        }
```
