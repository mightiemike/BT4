## Title
Non-atomic reentrancy-guard reset in the NEAR Wallet Contract permanently freezes the account if a callback receipt fails — (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The Vyper incident stemmed from an "improperly implemented reentrancy prevention" whose lock could be bypassed/left in an inconsistent state. The `WalletContract` (NEAR's ETH-emulation "wallet" global contract used by `eth-implicit` accounts) implements its own single-flight reentrancy guard, `has_in_flight_tx`, to serialize `rlp_execute` invocations [1](#0-0) . The invariant documented in the struct comment requires the flag to be `true` exactly while a promise is outstanding and `false` otherwise [2](#0-1) . However, the flag is reset to `false` as the *first* statement of each `#[private]` callback and is only re-armed to `true` later in the same function, all inside one receipt whose state changes are atomically committed or rolled back together. If anything in the remainder of that callback fails (most directly, gas exhaustion), the runtime rolls back the entire receipt — including the `has_in_flight_tx = false` reset — leaving the contract permanently locked.

### Finding Description
`rlp_execute` refuses to start a new transaction whenever `self.has_in_flight_tx` is `true`, and it is the *only* externally reachable entry point that clears the "in progress" condition on a fresh call [3](#0-2) . All of the callbacks that eventually clear/re-arm the flag (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`) are marked `#[private]`, meaning only the contract itself (as `predecessor_id`) can invoke them — i.e., only via a promise the contract previously scheduled [4](#0-3) [5](#0-4) [6](#0-5) .

Each of these callbacks begins by unconditionally writing `self.has_in_flight_tx = false;` before doing any further work that can fail (deserializing results, creating follow-up promises, doing refund transfers) [7](#0-6) [8](#0-7) [9](#0-8) .

Per the runtime's receipt execution model, a receipt's actions are executed and, on any action failure, the *entire* receipt's state changes are discarded via `state_update.rollback()` — there is no partial commit of "the flag reset happened, but the rest didn't": [10](#0-9) 

This means the `has_in_flight_tx = false` write at the top of any of these callbacks is not durable unless the whole callback function call succeeds. If the callback receipt fails for any reason (most realistically, running out of allocated gas while executing the later refund/promise-scheduling logic), the flag reverts to its prior value of `true` (it was `true` because a promise had to be outstanding to reach this callback at all, per the class invariant), and there is no other way to ever set it back to `false`: `rlp_execute` — the only externally reachable unlocker — immediately rejects all further calls while the flag is `true` [11](#0-10) , and the callbacks that could clear it can only be triggered as continuations of a promise chain that `rlp_execute` itself must first schedule.

The gas budget available to these callbacks is attacker-influenceable: a relayer submitting the RLP transaction only needs to satisfy `env::prepaid_gas() >= gas_limit * GAS_MULTIPLIER` [12](#0-11) , a check tied to the user's requested EVM `gas_limit`, not to any margin required for the wallet's own bookkeeping (nonce update, refund transfer, callback dispatch). A relayer can therefore attach the minimal amount of gas that still passes this check while leaving essentially no slack for the callback's own execution, driving it into `GasExceeded` failure mid-way through — after the flag reset has already executed logically but before the receipt commits.

### Impact Explanation
Once `has_in_flight_tx` is stuck `true`, the affected `eth-implicit` account can never again process a `rlp_execute` call: every future call is rejected at the very first check with "transaction already in progress" [11](#0-10) , and no legitimate path exists to clear the flag since the unlocking callbacks are `#[private]` and unreachable except through a new `rlp_execute` call. All of the account's $NEAR/NEP-141 balance that is only spendable through the wallet-contract's ETH-transaction-emulation interface becomes permanently frozen — this matches the explicitly accepted "permanently frozen funds" impact class.

### Likelihood Explanation
The failure trigger (gas exhaustion in a late-stage callback) is directly reachable from a single relayer-submitted, unprivileged transaction (`rlp_execute`), requires no validator or node-level access, and can be deliberately engineered by any relayer that controls the attached gas, or can occur accidentally whenever the attached gas is close to the protocol's minimum bound. The bug is a straightforward non-atomicity between "logical unlock" and "receipt commit," which is a persistent structural property of the contract's code, not a rare race condition.

### Recommendation
Redesign the guard so that clearing `has_in_flight_tx` cannot be rolled back together with fallible follow-up logic:
- Move the `has_in_flight_tx = false` write to the very end of each callback, after all fallible operations have succeeded, or
- Split "finish the current transaction" (safe, infallible clearing of the flag) into its own always-succeeding path/receipt that is scheduled independently of the fallible refund/continuation promises, or
- Provision callback static gas generously and independent of user-controlled minimums, and add an explicit gas floor check in `rlp_execute`/`inner_rlp_execute` ensuring sufficient slack gas remains for the wallet's own bookkeeping regardless of the user-requested `gas_limit`.

### Proof of Concept
1. Generate an `eth-implicit` account and deploy the Wallet Contract as in `test_wallet_contract_interaction` [13](#0-12) .
2. Register a relayer access key restricted to `rlp_execute` per `test_register_relayer` [14](#0-13) .
3. Sign (as the wallet owner) an Ethereum transaction whose emulated action is an ERC-20 transfer to an unregistered receiver, forcing the multi-step `nep_141_storage_balance_callback` → `storage_deposit` + `ft_transfer` → `rlp_execute_callback` chain [15](#0-14) .
4. As the relayer, submit the NEAR `rlp_execute` transaction attaching gas just above the minimum required by `validate_tx_relayer_data`'s check (`prepaid_gas >= gas_limit * GAS_MULTIPLIER`) [12](#0-11) , tuned so the gas allotted to the later `nep_141_storage_balance_callback`/`rlp_execute_callback` step is only just enough to begin executing but insufficient to complete (e.g. the double `function_call` chain plus refund transfer construction).
5. Observe that the callback receipt fails with a gas-exceeded error; per the runtime's atomic commit/rollback semantics [10](#0-9) , the entire receipt (including the earlier `self.has_in_flight_tx = false`) is rolled back, leaving the flag `true`.
6. Submit any subsequent, otherwise well-formed `rlp_execute` transaction and observe it is always rejected with `"transaction already in progress"` [11](#0-10) , confirming the wallet account's funds are now permanently inaccessible through its ETH-emulation interface.

### Citations

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L93-128)
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
    }
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L130-141)
```rust
    /// Callback after checking if an address is contained in the registrar.
    /// This check happens when the target is another eth implicit account to
    /// confirm that the relayer really did check for a named account with that address.
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L194-273)
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
        let current_account_id = env::current_account_id();
        let ext = WalletContract::ext(current_account_id).with_static_gas(RLP_EXECUTE_CALLBACK_GAS);
        let promise = match maybe_storage_balance {
            Some(_) => {
                // receiver_id is registered so we can send the transfer
                // without additional actions. Note: in the standard NEP-141
                // implementation it is impossible to have `Some` storage balance,
                // but have it be insufficient to transact.
                match action_to_promise(token_id, action)
                    .map(|p| p.then(ext.rlp_execute_callback(caller_deposit)))
                {
                    Ok(p) => p,
                    Err(e) => {
                        return PromiseOrValue::Value(e.into());
                    }
                }
            }
            None => {
                // receiver_id is not registered so we must call `storage_deposit` first.
                let storage_deposit_args =
                    format!(r#"{{"account_id": "{receiver_id}"}}"#).into_bytes();
                let transfer_function_call = match action {
                    near_action::Action::FunctionCall(x) => x,
                    _ => {
                        return PromiseOrValue::Value(ExecuteResponse {
                            success: false,
                            success_value: None,
                            error: Some(
                                "Expected function call action to perform NEP-141 transfer".into(),
                            ),
                        });
                    }
                };
                Promise::new(token_id)
                    .function_call(
                        "storage_deposit".into(),
                        storage_deposit_args,
                        NEP_141_STORAGE_DEPOSIT_AMOUNT,
                        NEP_141_STORAGE_DEPOSIT_GAS,
                    )
                    .function_call(
                        transfer_function_call.method_name,
                        transfer_function_call.args,
                        transfer_function_call.deposit,
                        transfer_function_call.gas,
                    )
                    .then(ext.rlp_execute_callback(caller_deposit))
            }
        };
        self.has_in_flight_tx = true;
        PromiseOrValue::Promise(promise)
    }
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

**File:** protocol-model/spec/runtime-execution.md (L69-70)
```markdown
6. **Refunds** (see below): system-predecessor receipts (refund receipts) are free — no refund generated, and a failed refund burns its deposit into `other_burnt_amount` (`runtime/runtime/src/lib.rs:929`). Otherwise `refund_unspent_gas_and_deposits` runs (`:943`).
7. **Commit or rollback**: success commits with `ReceiptProcessing`; failure calls `state_update.rollback()`, discarding all state changes from the receipt (`runtime/runtime/src/lib.rs:961`-`970`).
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs (L361-365)
```rust
    // Relayers must attach at least as much gas as the user requested.
    let gas_limit = if tx.gas_limit < U64_MAX { tx.gas_limit.as_u64() } else { u64::MAX };
    if env::prepaid_gas().as_gas() < gas_limit.saturating_mul(GAS_MULTIPLIER) {
        return Err(Error::Relayer(RelayerError::InsufficientGas));
    }
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/relayer.rs (L25-52)
```rust
#[tokio::test]
async fn test_register_relayer() -> anyhow::Result<()> {
    let TestContext { worker, mut wallet_contract, wallet_sk, .. } = TestContext::new().await?;

    let relayer_pk = wallet_contract.register_relayer(&worker).await?;
    let key = wallet_contract.inner.as_account().view_access_key(&relayer_pk).await?;
    match &key.permission {
        AccessKeyPermission::FunctionCall(access) => {
            assert_eq!(access.allowance, None);
            assert_eq!(access.receiver_id.as_str(), wallet_contract.inner.id().as_str());
            assert_eq!(&access.method_names, &[RLP_EXECUTE]);
        }
        _ => panic!("Unexpected full access key"),
    }

    // Should be able to submit transactions using the new key
    utils::deploy_and_call_hello(&worker, &wallet_contract, &wallet_sk, 1).await?;

    // If the relayer is dishonest then its key is revoked.
    // In this case the relayer will try to repeat a nonce value.
    let result = utils::deploy_and_call_hello(&worker, &wallet_contract, &wallet_sk, 1).await;
    let error_message = format!("{:?}", result.unwrap_err());
    assert!(error_message.contains("faulty relayer"));

    assert_revoked_key(&wallet_contract.inner, &relayer_pk).await;

    Ok(())
}
```
