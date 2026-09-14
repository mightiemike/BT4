### Title
Wallet Contract `has_in_flight_tx` Flag Can Be Permanently Stuck, Freezing the Account's `rlp_execute` Pathway - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The `WalletContract` (the ETH-implicit-account global contract used to emulate Ethereum transactions on NEAR) uses a `has_in_flight_tx` boolean to serialize execution: `rlp_execute` refuses to start a new action while a previous one is still in flight, and every callback (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`) is responsible for resetting it back to `false`. If the *callback* receipt itself fails (e.g. because its statically-reserved gas budget is exhausted while decoding an oversized `PromiseResult::Successful` value returned by the called contract), the state mutation that resets `has_in_flight_tx` is rolled back along with the rest of the failed receipt, but the initial `rlp_execute`/`inner_rlp_execute` receipt that already set `has_in_flight_tx = true` and burned its gas has already committed. This leaves the flag permanently `true`, with no recovery mechanism, analogous to the reported `entropyCallback`/`whenNotPaused` issue where a completed, fee-consuming request can never reach a state-resetting callback.

### Finding Description
`rlp_execute` sets `self.has_in_flight_tx = true` before returning `PromiseOrValue::Promise(promise)` [1](#0-0) . The struct's own doc-comment states the invariant explicitly: "`has_in_flight_tx` must be `true` when a mutable method of this contract returns a promise and `false` otherwise" [2](#0-1) .

Every callback that is `.then()`-chained onto the action promise is supposed to reset the flag as its first statement, e.g. `rlp_execute_callback`:
```
self.has_in_flight_tx = false;
``` [3](#0-2) 

However, each callback is scheduled with a fixed, small static gas allocation, e.g. `RLP_EXECUTE_CALLBACK_GAS: Gas = Gas::from_tgas(5)` [4](#0-3) , and the callback body must read `env::promise_result(0)` and process whatever data the *target* contract/action returned [5](#0-4) . Copying/deserializing a `PromiseResult::Successful(value)` costs gas proportional to the size of `value`; if the receiver contract targeted by the relayer-submitted RLP transaction returns a sufficiently large successful value, the callback receipt's execution can exceed its statically reserved 5 Tgas budget and fail with "Exceeded the prepaid gas." When a receipt fails, its state changes — including the `self.has_in_flight_tx = false` write — are rolled back, exactly as the runtime spec confirms: "failure calls `state_update.rollback()`, discarding all state changes from the receipt" (`runtime/runtime/src/lib.rs:961`) [6](#0-5) .

Because `has_in_flight_tx` was already committed as `true` by the earlier (successful) `rlp_execute` receipt, and no code path exists to reset it once its resetting callback itself fails, the flag becomes permanently stuck at `true`. Every subsequent call to `rlp_execute` on that account is then unconditionally rejected: `if self.has_in_flight_tx { return ...Error("transaction already in progress...") }` [7](#0-6) .

This is structurally the same bug class as the reported `entropyCallback`: a two-phase flow where phase 1 (request, fee/gas already spent) commits state, but phase 2 (the callback that is supposed to finalize/reset state) can fail to run to completion, and no recovery mechanism resets the stuck state. Unlike the reported issue — where an admin `refundGame` exists — there is **no** operator-controlled recovery for `has_in_flight_tx` at all; it is pure contract-internal state with no privileged reset method.

### Impact Explanation
Eth-implicit accounts are provisioned so that relayer access keys are restricted via `AccessKeyPermission::FunctionCall` to the single method `rlp_execute` [8](#0-7) . Once `has_in_flight_tx` is stuck `true`, that method permanently refuses to execute any further action for the account (the "already in progress" branch is unconditional and has no timeout or expiry), which permanently freezes the ability to move funds or execute contract calls through this pathway for that eth-implicit account — a concrete "permanently frozen funds" outcome reachable purely by a normal relayer submitting a legitimately signed user transaction whose target returns an oversized successful value.

### Likelihood Explanation
Triggering this requires only: (1) a relayer/user submits an `rlp_execute` transaction whose target action calls into a contract that can be made to return a large `Successful` payload (many contracts return log/output blobs, and a user/attacker interacting with their own eth-implicit account can pick the target contract and method freely), and (2) the static callback gas (5 Tgas, or `RLP_EXECUTE_CALLBACK_GAS`/`NEP_141_STORAGE_BALANCE_CALLBACK_GAS`/`ADDRESS_CHECK_CALLBACK_GAS`, all similarly small fixed budgets) is insufficient to decode that payload. No validator, node, or protocol-level privilege is needed — a single unprivileged transaction signer/relayer can reach this path, and self-triggering it against one's own account is trivial to test deterministically by choosing a large enough return value.

### Recommendation
- Size the callback's static gas allocation based on the caller-controlled data it must process (e.g., bound/validate the size of values returned by inner promises before scheduling the callback, or reserve gas proportional to the actual attached gas of the inner call rather than a small fixed constant).
- Add a way to recover from a stuck `has_in_flight_tx`: e.g., persist a timestamp/block height when the flag is set and allow `rlp_execute` (or a dedicated recovery method) to proceed if the in-flight transaction has clearly failed to resolve within a bounded number of blocks, mirroring how `PromiseYieldTimeout` guarantees resolution for yielded promises [9](#0-8) .
- Alternatively, ensure the flag-reset is committed independently of the potentially-failing decode logic (e.g., reset `has_in_flight_tx` in a receipt/action that is guaranteed to succeed regardless of the size of the returned payload).

### Proof of Concept
1. Deploy the `WalletContract` as a global contract for an eth-implicit account, as in `test_wallet_contract_interaction` [10](#0-9) .
2. Deploy (or reuse) a target contract whose method, when called, returns a large successful value (e.g. tens of KB of data serialized as the return value).
3. Sign and submit (via `create_rlp_execute_tx`) an RLP-encoded Ethereum `FunctionCall` transaction routed through `rlp_execute` targeting that method, with the action's own attached gas set high enough for the inner call to succeed but leaving the outer `RLP_EXECUTE_CALLBACK_GAS`/`NEP_141_STORAGE_BALANCE_CALLBACK_GAS` (5 Tgas) allocation insufficient to process the returned value inside `rlp_execute_callback`/`nep_141_storage_balance_callback`.
4. Observe: the inner call succeeds and burns gas; the callback receipt fails with an "Exceeded the prepaid gas" error; `has_in_flight_tx` remains `true` in contract state (rolled back reset).
5. Submit any subsequent `rlp_execute` transaction for the same account — observe it is permanently rejected with `"Error: transaction already in progress, please try again later."`, with no way to clear the flag, analogous to the existing `test_simultaneous_transactions` test that demonstrates the "already in progress" rejection path but does not exercise the permanent-stuck scenario [11](#0-10) .

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L97-105)
```rust
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L275-316)
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
        } else if n > 1 {
            return ExecuteResponse {
                success: false,
                success_value: None,
                error: Some(format!(
                    "Invariant violation: this callback comes after a single promise. n={n}"
                )),
            };
        }

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
```

**File:** protocol-model/spec/runtime-execution.md (L70-70)
```markdown
7. **Commit or rollback**: success commits with `ReceiptProcessing`; failure calls `state_update.rollback()`, discarding all state changes from the receipt (`runtime/runtime/src/lib.rs:961`-`970`).
```

**File:** integration-tests/src/tests/features/wallet_contract.rs (L227-304)
```rust
#[test]
fn test_wallet_contract_interaction() {
    let genesis = Genesis::test(vec!["test0".parse().unwrap(), alice_account(), bob_account()], 1);
    let mut env = TestEnv::builder(&genesis.config).nightshade_runtimes(&genesis).build();

    let genesis_block = env.clients[0].chain.get_block_by_height(0).unwrap();
    let chain_id = &genesis.config.chain_id;
    let mut height = 1;
    let blocks_number = 10;

    // As the relayer, alice will be sending Near transactions which
    // contain the Ethereum transactions the user signs.
    let relayer = alice_account();
    let mut relayer_signer =
        NearSigner { account_id: &relayer, signer: create_user_test_signer(&relayer) };
    // Bob will receive a $NEAR transfer from the eth implicit account
    let receiver = bob_account();

    // Deploy the wallet contract as a global contract for ETH implicit accounts.
    let magic_bytes = wallet_contract_magic_bytes(chain_id);
    let wallet_code = wallet_contract(*magic_bytes.hash()).unwrap();
    let deploy_tx = SignedTransaction::deploy_global_contract(
        1,
        relayer.clone(),
        wallet_code.code().to_vec(),
        &relayer_signer.signer,
        *genesis_block.hash(),
        GlobalContractDeployMode::CodeHash,
    );
    height = check_tx_processing(&mut env, deploy_tx, height, blocks_number);

    // Generate an eth implicit account for the user
    let secret_key = SecretKey::from_seed(KeyType::SECP256K1, "test");
    let public_key = secret_key.public_key();
    let eth_implicit_account = derive_eth_implicit_account_id(public_key.unwrap_as_secp256k1());

    // Create ETH-implicit account by funding it.
    // Although ETH-implicit account can be zero-balance, we pick a non-zero amount
    // here in order to make transfer later from this account.
    let deposit_for_account_creation = Balance::from_near(1);
    let actions = vec![Action::Transfer(TransferAction { deposit: deposit_for_account_creation })];
    let block_hash = *genesis_block.hash();
    let nonce = 2;
    let signed_transaction = SignedTransaction::from_actions(
        nonce,
        relayer.clone(),
        eth_implicit_account.clone(),
        &relayer_signer.signer.clone().into(),
        actions,
        block_hash,
    );
    height = check_tx_processing(&mut env, signed_transaction, height, blocks_number);

    // The relayer adds its key to the eth implicit account so that
    // can sign Near transactions for the user.
    let relayer_pk = relayer_signer.signer.public_key();
    let action = Action::AddKey(Box::new(AddKeyAction {
        public_key: relayer_pk,
        access_key: AccessKey {
            nonce: 0,
            permission: AccessKeyPermission::FunctionCall(FunctionCallPermission {
                allowance: None,
                receiver_id: eth_implicit_account.to_string(),
                method_names: vec!["rlp_execute".into()],
            }),
        },
    }));
    let signed_transaction = create_rlp_execute_tx(
        &eth_implicit_account,
        action,
        0,
        &eth_implicit_account,
        &secret_key,
        &mut relayer_signer,
        &env,
    );
    let prepaid_gas = total_prepaid_gas(signed_transaction.transaction.actions()).unwrap();
    height = check_tx_processing(&mut env, signed_transaction, height, blocks_number);
```

**File:** runtime/runtime/src/lib.rs (L3113-3131)
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
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L121-168)
```rust
/// Only one transaction can be in flight at a time.
#[tokio::test]
async fn test_simultaneous_transactions() -> anyhow::Result<()> {
    let TestContext { worker, wallet_contract, wallet_sk, .. } = TestContext::new().await?;

    let receiver_account = worker.root_account().unwrap();

    let initial_receiver_balance = receiver_account.view_account().await.unwrap().balance;

    let receiver_id = receiver_account.id().as_str().into();
    let action = Action::Transfer { receiver_id, yocto_near: 1 };
    let signed_transaction =
        utils::create_signed_transaction(0, receiver_account.id(), Wei::zero(), action, &wallet_sk);
    let wallet_method_call_1 = near_workspaces::operations::Function::new("rlp_execute")
        .args_json(serde_json::json!({
            "target": receiver_account.id(),
            "tx_bytes_b64": codec::encode_b64(&codec::rlp_encode(&signed_transaction))
        }))
        .gas(near_workspaces::types::Gas::from_tgas(100));
    let wallet_method_call_2 = near_workspaces::operations::Function::new("rlp_execute")
        .args_json(serde_json::json!({
            "target": receiver_account.id(),
            "tx_bytes_b64": codec::encode_b64(&codec::rlp_encode(&signed_transaction))
        }))
        .gas(near_workspaces::types::Gas::from_tgas(100));

    let near_transaction = wallet_contract
        .inner
        .as_account()
        .batch(wallet_contract.inner.id())
        .call(wallet_method_call_1)
        .call(wallet_method_call_2)
        .transact()
        .await?;

    let result: ExecuteResponse = near_transaction.json()?;

    // The second transaction in the batch fails and this is returned as the
    // result of the Near transaction. But the first transaction in the batch
    // spawns promises that resolve, so the transfer was will successful.
    assert!(!result.success);
    assert!(result.error.unwrap().contains("transaction already in progress"));

    let final_receiver_balance = receiver_account.view_account().await.unwrap().balance;
    assert_eq!(final_receiver_balance.as_yoctonear() - initial_receiver_balance.as_yoctonear(), 1,);

    Ok(())
}
```
