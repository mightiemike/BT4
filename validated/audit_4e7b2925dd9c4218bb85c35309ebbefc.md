### Title
Wallet Contract callbacks use hardcoded fixed static gas, risking permanent lock of ETH-implicit accounts if gas costs increase - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (deployed on every ETH-implicit account per NEP-518) attaches hardcoded, fixed `static_gas` budgets to its internal callback promises (`RLP_EXECUTE_CALLBACK_GAS`, `ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`, etc., all derived from `Gas::from_tgas(5)`). These fixed amounts are never scaled to the runtime's parameterized gas cost config, which is exactly the "stop depending on fixed gas" anti-pattern the source report warns about (analogous to Solidity's fixed-gas-stipend `.transfer()` breaking after EIP-1884 repricing). Because the `has_in_flight_tx` re-entrancy guard is only ever reset to `false` inside these gas-capped private callbacks, an insufficient fixed gas budget (e.g. after a future protocol-level increase to WASM/host-function/action gas costs) can cause the callback receipt to fail before its body executes, permanently stranding `has_in_flight_tx = true` and locking the account.

### Finding Description
`runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs` defines several hardcoded `Gas` constants used as `with_static_gas(...)` budgets for the contract's own callback methods: [1](#0-0) 

These are attached to promises that call back into the wallet contract itself (`rlp_execute_callback`, `address_check_callback`, `nep_141_storage_balance_callback`), for example: [2](#0-1) 

Each of these private callbacks begins by clearing the in-flight guard: [3](#0-2) [4](#0-3) [5](#0-4) 

`has_in_flight_tx` is set to `true` before any of these promises are dispatched and is only ever reset to `false` from inside one of these gas-capped callbacks: [6](#0-5) 

`rlp_execute` unconditionally rejects any further calls while `has_in_flight_tx` is `true`, with no other method available to clear it: [7](#0-6) 

The actual gas required to load/deserialize contract state, dispatch a NEAR method, and execute host functions is defined by the runtime's `RuntimeConfig`/`ExtCostsConfig`/`ActionCosts` parameters (e.g. `function_call_base`, `wasm_base`, `wasm_contract_loading_base`, etc.), which are explicitly designed to change across protocol versions: [8](#0-7) 

The parameter snapshots in `core/parameters/src/snapshots/` show these very costs (`function_call_cost`, `deploy_contract_cost_per_byte`, etc.) have indeed changed multiple times across protocol versions, confirming that gas costs in nearcore are not fixed over time.

### Impact Explanation
If a future protocol upgrade increases the gas cost of basic contract dispatch/execution (deserializing the `WalletContract` state, host-function calls, WASM base fees, etc.) such that it exceeds the hardcoded `5 Tgas` static-gas budget attached to a callback, the callback receipt will fail with an out-of-gas error before executing its body — i.e. before `self.has_in_flight_tx = false;` runs. Since this flag can only be reset from inside these very callbacks, and `rlp_execute` refuses to process any transaction while it is `true`, the ETH-implicit account becomes permanently unable to process any further Ethereum-emulated transactions. This is a transaction-triggered, permanent denial of service / freezing of funds and functionality for the affected account, matching the "permanently frozen funds" / "transaction-triggered halt" impact classes, since NEAR tokens or NEP-141 assets already deposited to that ETH-implicit account can no longer be moved via the only interface (`rlp_execute`) available for that account type.

### Likelihood Explanation
The trigger condition depends on a future protocol-level gas repricing (a normal, expected event in nearcore's history, as shown by the parameter snapshots) combined with the currently minimal `5 Tgas` safety margin baked into these constants. The report explicitly flags this exact risk class (fixed gas assumptions breaking after gas repricing). Given nearcore has changed action/host-function gas costs multiple times already, and the Wallet Contract's fixed budgets have no buffer or dynamic scaling mechanism, likelihood of eventually triggering this is non-negligible on any long-lived protocol, though it requires either a gas cost increase or interaction with unusually expensive external NEP-141 contracts during `storage_balance_of`/`storage_deposit`/registrar lookups that are also gas-capped by fixed constants.

### Recommendation
- Avoid hardcoding fixed `Gas::from_tgas(N)` constants for internal callback static gas. Instead, use `promise_batch_action_function_call_weight` / `function_call_weight` (NEP-264 unspent-gas-ratio attachment) so the callback receives a fraction of whatever gas remains, automatically scaling with future cost changes.
- If fixed budgets must be kept for external/untrusted calls (e.g. `storage_balance_of` on arbitrary NEP-141 contracts), add a wide safety margin and make them configurable/upgradable rather than compiled-in constants.
- Ensure `has_in_flight_tx` can be recovered through an alternate path (e.g. a time-based/expiry mechanism or an owner-only reset) in case a callback never executes, so a single out-of-gas failure cannot permanently brick the account.

### Proof of Concept
1. Deploy the Wallet Contract on an ETH-implicit account (as done in `runtime/near-wallet-contract/implementation/wallet-contract/src/tests/utils/test_context.rs`).
2. Simulate (or wait for) a protocol upgrade that increases baseline WASM/function-dispatch gas costs (as recorded historically in `core/parameters/src/snapshots/near_parameters__config_store__tests__*.json.snap`, where `function_call_cost.execution` values change across versions) such that executing `rlp_execute_callback`/`address_check_callback`/`nep_141_storage_balance_callback` requires more gas than the hardcoded `RLP_EXECUTE_CALLBACK_GAS`/`ADDRESS_CHECK_CALLBACK_GAS`/`NEP_141_STORAGE_BALANCE_CALLBACK_GAS` constants (`Gas::from_tgas(5)`-based) provide.
3. Call `rlp_execute` with a valid signed Ethereum-style transaction (e.g. a base token transfer via `test_wallet_contract_interaction` in `integration-tests/src/tests/features/wallet_contract.rs`), setting `has_in_flight_tx = true`.
4. The scheduled callback fails with `FunctionCallError::HostError(GasExceeded)` before reaching `self.has_in_flight_tx = false;`.
5. Any subsequent call to `rlp_execute` on that account now unconditionally returns `"Error: transaction already in progress, please try again later."` forever, permanently freezing the account.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L94-127)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-203)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L275-285)
```rust
    #[private]
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
        let n = env::promise_results_count();

        if n == 0 {
            // `rlp_execute_callback` is called directly in the case of an emulated self-transfer.
            return ExecuteResponse { success: true, success_value: None, error: None };
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L459-471)
```rust
        TransactionKind::EthEmulation(EthEmulationKind::SelfBaseTokenTransfer) => {
            // Base token transfers to self are no-ops on Near, so we do not need to
            // schedule an additional call. We can simply go straight to `rlp_execute_callback`.
            let ext: WalletContractExt =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            ext.rlp_execute_callback(caller_deposit)
        }
        _ => {
            let ext =
                WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
            action_to_promise(target, action)?.then(ext.rlp_execute_callback(caller_deposit))
        }
    };
```

**File:** core/parameters/res/runtime_configs/parameters.yaml (L174-180)
```yaml
# Smart contract dynamic gas costs
wasm_regular_op_cost: 822_756
wasm_linear_op_base_cost: 300_000_000_000_000
wasm_linear_op_unit_cost: 300_000_000_000_000
wasm_grow_mem_cost: 1
wasm_base: 264_768_111
wasm_contract_loading_base: 35_445_963
```
