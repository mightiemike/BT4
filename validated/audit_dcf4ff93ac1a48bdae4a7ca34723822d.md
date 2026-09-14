### Title
Wallet Contract Callback Panic Permanently Locks `has_in_flight_tx`, Freezing All Wallet Funds - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The near-wallet-contract (used for ETH-implicit accounts, deployed as a global contract per NEP for Ethereum-style transaction execution) guards re-entrancy with a single boolean flag `has_in_flight_tx`. This flag is set to `true` when `rlp_execute` schedules a promise, and is only ever reset to `false` as the *first statement* of a follow-up callback (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`). If that callback receipt fails for any reason before its state changes commit, `has_in_flight_tx` remains permanently `true`, and `rlp_execute` — the sole entry point that can move funds out of the wallet — will reject every future call with `"transaction already in progress"`, forever.

### Finding Description
`WalletContract::rlp_execute` refuses to start a new transaction while one is in flight: [1](#0-0) 

When `inner_rlp_execute` succeeds it sets the flag and returns a `Promise`: [2](#0-1) 

The flag is only cleared inside callbacks, and only as their very first action, e.g. in `nep_141_storage_balance_callback`: [3](#0-2) 

and `address_check_callback`: [4](#0-3) 

On NEAR, if a function-call receipt fails/panics (including "Exceeded the prepaid gas"), all state writes made during that receipt — including the `has_in_flight_tx = false` write made at the top of the callback — are discarded; only the prior, already-committed `has_in_flight_tx = true` from the earlier receipt survives. The comment on the struct itself documents the fragile invariant this relies on: [5](#0-4) 

`nep_141_storage_balance_callback` receives the byte response of `storage_balance_of` from `token_id`, which — for an ERC-20-emulated transfer — is the arbitrary NEP-141 contract encoded as the Ethereum transaction's `to` address (i.e. whatever contract the signed transaction targets). Retrieving/deserializing an oversized or malformed response from that contract inside the fixed, hardcoded gas budget (`NEP_141_STORAGE_BALANCE_CALLBACK_GAS`, a small constant) can exceed the attached gas and cause the callback receipt itself to fail before the `has_in_flight_tx = false` write commits: [6](#0-5) 

Once this happens, every subsequent call to `rlp_execute` — the only mechanism to move funds or perform any action from the wallet — is unconditionally rejected, permanently freezing the wallet's balance and control.

### Impact Explanation
The wallet contract is deployed as a global contract referenced by ETH-implicit accounts and holds the user's $NEAR (and can hold/transfer NEP-141 tokens via emulation). If `has_in_flight_tx` becomes permanently stuck `true`, the account is permanently frozen: no further `rlp_execute` call can ever succeed, so the owner can never again transfer NEAR, call contracts, add/remove keys, or transfer tokens from that address. This is a permanent denial of the account's funds and functionality, analogous to the reported "permanently bricked contract" bug class, reachable purely through normal (if adversarially-crafted-target) transaction submission — no validator, network, or operator privilege required.

### Likelihood Explanation
Triggering it requires the wallet owner (or a relayer acting on their signed transaction) to interact with a malicious/adversarial NEP-141-emulated token contract that returns an oversized or gas-expensive `storage_balance_of` response, causing the fixed-gas callback receipt to exceed its budget and fail. This is plausible via a malicious token/dApp enticing users to a "transfer" interaction, but requires a specific adversarial counter-contract and gas-budget conditions rather than being trivially triggerable by anyone against any wallet at will, so likelihood is moderate rather than trivial.

### Recommendation
- Do not rely on a callback's *first statement* to restore a re-entrancy guard; instead reset `has_in_flight_tx` in a way that is robust to the callback panicking, e.g. by using `#[near_bindgen(callback)]`/`env::promise_result` size limits, or wrapping cross-contract response handling so any failure to parse/consume the response cannot cause the whole receipt (and its state writes) to be discarded.
- Bound the maximum bytes read from `promise_result` before attempting to deserialize (reject overly large responses early with a cheap length check) so gas cannot be exhausted by an oversized malicious response.
- Provide a permissionless "unstick" mechanism (e.g., a method that lets the account clear `has_in_flight_tx` after a timeout or once no promises are genuinely pending) so a failed callback cannot permanently brick the account.

### Proof of Concept
1. Attacker deploys a NEP-141-look-alike contract `evil.near` whose `storage_balance_of` method returns an artificially large/expensive-to-deserialize response (e.g., megabytes of data or nested structures) instead of the expected `Option<StorageBalance>`.
2. The wallet owner signs (or is convinced to sign, e.g., via a malicious dApp/relayer flow) an ERC-20-emulated `transfer` Ethereum transaction whose `to` address maps to `evil.near`.
3. The transaction is submitted via `rlp_execute`; `inner_rlp_execute` succeeds, `has_in_flight_tx = true` commits, and the promise chain calls `evil.near::storage_balance_of` then schedules `nep_141_storage_balance_callback` with the fixed `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`.
4. `evil.near` returns its oversized payload; loading/deserializing it inside the callback's fixed gas budget exceeds prepaid gas, and the callback receipt fails with "Exceeded the prepaid gas" — its state changes (including the pending `has_in_flight_tx = false`) are discarded, leaving the already-committed `has_in_flight_tx = true` in place.
5. Any subsequent call to `rlp_execute` on this wallet now unconditionally returns `"Error: transaction already in progress, please try again later."` forever, permanently freezing the account's funds and control.

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L46-55)
```rust
pub struct WalletContract {
    pub nonce: u64,
    /// Tracks whether a transaction is currently being executed
    /// (i.e. has receipts that have not yet resolved).
    /// Invariant: `has_in_flight_tx` must be `true` when a mutable method
    /// of this contract returns a promise and `false` otherwise (except
    /// for the check if a transaction is already in flight at the beginning
    /// of `rlp_execute`).
    pub has_in_flight_tx: bool,
}
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-221)
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
```
