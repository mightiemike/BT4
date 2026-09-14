## Title
Wallet Contract `has_in_flight_tx` guard can be permanently stuck at `true`, bricking an ETH-implicit account forever - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The Safe finding is about a single-signer-settable "guard" that intercepts *every* execution path (including recovery paths), so a broken guard permanently bricks the account because there is no way left to unset it. The closest reachable analog in nearcore is the NEAR Wallet Contract used for ETH-implicit accounts: these accounts have **no access key at all** and can only ever be operated by calling `rlp_execute` on the Wallet Contract [1](#0-0) . Every mutating entry point is gated by a single boolean flag, `has_in_flight_tx`, that must be reset to `false` by a follow-up callback before any further `rlp_execute` call is accepted [2](#0-1) [3](#0-2) .

### Finding Description
`rlp_execute` refuses to do anything if `has_in_flight_tx` is `true`, and otherwise sets it to `true` before returning a cross-contract `Promise` chain [4](#0-3) . The flag is only ever cleared inside `#[private]` callbacks (`rlp_execute_callback`, `address_check_callback`, `nep_141_storage_balance_callback`, `ban_relayer`), and in each case the reset (`self.has_in_flight_tx = false;`) is the *first* statement of the callback body [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) .

On NEAR, a function-call receipt's state mutations are only committed if the call returns normally; if the call panics/aborts for any reason (including running out of the fixed, hard-coded gas budget attached to the callback, e.g. `RLP_EXECUTE_CALLBACK_GAS`, `ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`), **all state writes from that call are rolled back**, including the `has_in_flight_tx = false` write that happened at the very start of the function. Both `address_check_callback` and `nep_141_storage_balance_callback` perform `serde_json::from_slice` on a value returned by an externally-called contract (`address_registrar.lookup` or `token_id.storage_balance_of`) before doing anything else useful [9](#0-8) [10](#0-9) . The account/contract targeted for a NEP-141 style transfer (`token_id`) is derived directly from the RLP-encoded Ethereum transaction the wallet owner signs, i.e., it is attacker/target-contract-controlled data flowing into that callback's cross-contract response. If any such callback exceeds its statically attached gas budget (e.g. because the callee returns an unusually large or expensive-to-deserialize payload), the callback aborts, its `has_in_flight_tx = false` write never commits, and the flag is left permanently `true`.

Because `rlp_execute` unconditionally rejects new calls whenever `has_in_flight_tx == true` [3](#0-2) , and this is the *only* entry point the account can use (ETH-implicit accounts cannot hold a full-access key and cannot deploy a different contract — see `test_cannot_add_full_access_key` and `test_transaction_from_eth_implicit_account_fail` [11](#0-10) [12](#0-11) ), a stuck flag has the same effect as the Safe report's broken guard: every recovery path is gated by the same broken mechanism, so the account and any funds held on it become permanently inaccessible.

### Impact Explanation
If reachable, this is a **High** severity issue: it results in permanently frozen funds with no privileged recovery path, matching the report's "user funds permanently frozen" impact criterion. The account can never again execute `rlp_execute` (the sole path to move funds, add a relayer key, or otherwise act), and there is no module/guard-management analog in NEAR's account model to intervene, since `AccountContract::Global` code cannot be replaced by the owner (no full-access key is ever installable on ETH-implicit accounts) [13](#0-12) .

### Likelihood Explanation
This is **not confirmed** from the available code and index alone — it depends on whether the fixed gas budgets (`RLP_EXECUTE_CALLBACK_GAS = 5 Tgas`, `ADDRESS_CHECK_CALLBACK_GAS`, `NEP_141_STORAGE_BALANCE_CALLBACK_GAS`) can actually be exceeded by attacker-influenced callback inputs (e.g., an oversized/malformed JSON response from a NEP-141-like contract during `serde_json::from_slice`), or by any other panic path inside these `#[private]` callbacks. I could not find a test in the indexed code that specifically exercises "callback panics mid-execution" to confirm or refute that the flag reset is rolled back with it. The existing test `test_insufficient_gas` only demonstrates that insufficient gas on the *outer* `rlp_execute` call fails gracefully without setting the flag at all — it does not test insufficient gas mid-callback (i.e., after `has_in_flight_tx` was already set to `true` by a prior call) [14](#0-13) .

### Recommendation
Add a test that forces a callback (`rlp_execute_callback`, `address_check_callback`, or `nep_141_storage_balance_callback`) to run out of gas or panic after `has_in_flight_tx` has been set to `true`, and confirm the account is not left permanently unusable. If it is confirmed stuck, consider: (a) resetting `has_in_flight_tx` via a separate top-level scheduled callback that does not depend on completing the same execution path that can fail, (b) adding a time-based or nonce-based escape hatch that allows a new `rlp_execute` call to proceed if the in-flight transaction has been outstanding longer than is plausible for legitimate execution, or (c) ensuring the flag write is committed independently of the risky deserialization/host-call logic (e.g., write-then-checkpoint before doing any externally-influenced parsing).

### Proof of Concept
Not constructed — this would require a background Devin session with access to a local sandbox/testnet to (1) deploy the Wallet Contract, (2) craft a NEP-141-style token contract whose `storage_balance_of` response is deliberately oversized/expensive to deserialize, (3) drive `rlp_execute` through the NEP-141 transfer path so `nep_141_storage_balance_callback` is invoked with a limited `NEP_141_STORAGE_BALANCE_CALLBACK_GAS` budget, and (4) verify whether the callback aborts before the `has_in_flight_tx = false` write commits, leaving the account permanently unable to call `rlp_execute` again.

### Citations

**File:** docs/DataStructures/Account.md (L115-122)
```markdown
- If this is ETH-implicit account, it will have the [Wallet Contract](#wallet-contract) deployed, which can only be used by the owner of the Secp256K1 private key where `'0x' + keccak256(public_key)[12:32].hex()` matches the account ID.

Implicit account can not be created using `CreateAccount` action to avoid being able to hijack the account without having the corresponding private key.

Once a NEAR-implicit account is created it acts as a regular account until it's deleted.

An ETH-implicit account can only be used by calling the methods of the [Wallet Contract](#wallet-contract). It cannot be deleted, nor can a full access key be added.
The primary purpose of ETH-implicit accounts is to enable seamless integration of existing Ethereum tools (such as wallets) with the NEAR blockchain.
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L133-140)
```rust
    #[private]
    pub fn address_check_callback(
        &mut self,
        target: AccountId,
        action: near_action::Action,
        caller_deposit: Option<CallerDeposit>,
    ) -> PromiseOrValue<ExecuteResponse> {
        self.has_in_flight_tx = false;
```

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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L275-280)
```rust
    #[private]
    pub fn rlp_execute_callback(
        &mut self,
        caller_deposit: Option<CallerDeposit>,
    ) -> ExecuteResponse {
        self.has_in_flight_tx = false;
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/user_error.rs (L213-253)
```rust
// User's are not allowed to add full access keys to the account.
// This would be too dangerous as it could allow for undefined behaviour
// such as deploying a different contract to an Eth implicit address.
#[tokio::test]
async fn test_cannot_add_full_access_key() -> anyhow::Result<()> {
    let TestContext { wallet_contract, wallet_sk, .. } = TestContext::new().await?;

    let key = SecretKey::from_random(KeyType::ED25519);
    let action = Action::AddKey {
        public_key_kind: 0,
        public_key: key.public_key().key_data().to_vec(),
        nonce: 0,
        is_full_access: true,
        is_limited_allowance: false,
        allowance: 0,
        receiver_id: String::new(),
        method_names: Vec::new(),
    };
    let signed_transaction = utils::create_signed_transaction(
        0,
        wallet_contract.inner.id(),
        Wei::zero(),
        action,
        &wallet_sk,
    );

    let result = wallet_contract
        .rlp_execute(wallet_contract.inner.id().as_str(), &signed_transaction)
        .await?;

    assert!(!result.success);
    assert_eq!(
        result.error,
        Some(
            Error::User(UserError::UnsupportedAction(UnsupportedAction::AddFullAccessKey))
                .to_string()
        )
    );

    Ok(())
}
```

**File:** integration-tests/src/tests/features/wallet_contract.rs (L131-225)
```rust
/// Test that transactions from ETH-implicit accounts are rejected.
#[test]
fn test_transaction_from_eth_implicit_account_fail() {
    let genesis = Genesis::test(vec!["test0".parse().unwrap(), "test1".parse().unwrap()], 1);
    let mut env = TestEnv::builder(&genesis.config).nightshade_runtimes(&genesis).build();
    let genesis_block = env.clients[0].chain.get_block_by_height(0).unwrap();
    let chain_id = &genesis.config.chain_id;
    let deposit_for_account_creation = Balance::from_near(1);
    let mut height = 1;
    let blocks_number = 5;
    let signer1 = InMemorySigner::test_signer(&"test1".parse().unwrap());

    let secret_key = SecretKey::from_seed(KeyType::SECP256K1, "test");
    let public_key = secret_key.public_key();
    let eth_implicit_account_id = derive_eth_implicit_account_id(public_key.unwrap_as_secp256k1());
    let eth_implicit_account_signer =
        InMemorySigner::from_secret_key(eth_implicit_account_id.clone(), secret_key).into();

    // Send money to ETH-implicit account, invoking its creation.
    let send_money_tx = SignedTransaction::send_money(
        1,
        "test1".parse().unwrap(),
        eth_implicit_account_id.clone(),
        &signer1,
        deposit_for_account_creation,
        *genesis_block.hash(),
    );
    // Check for tx success status and get new block height.
    height = check_tx_processing(&mut env, send_money_tx, height, blocks_number);
    let block = env.clients[0].chain.get_block_by_height(height - 1).unwrap();

    // Try to send money from ETH-implicit account using `(block_height - 1) * 1e6` as a nonce.
    // That would be a good nonce for any access key, but the transaction should fail nonetheless because there is no access key.
    let nonce = (height - 1) * AccessKey::ACCESS_KEY_NONCE_RANGE_MULTIPLIER;
    let send_money_from_eth_implicit_account_tx = SignedTransaction::send_money(
        nonce,
        eth_implicit_account_id.clone(),
        "test0".parse().unwrap(),
        &eth_implicit_account_signer,
        Balance::from_yoctonear(100),
        *block.hash(),
    );
    let response =
        env.rpc_handlers[0].process_tx(send_money_from_eth_implicit_account_tx, false, false);
    let expected_tx_error = ProcessTxResponse::InvalidTx(InvalidTxError::InvalidAccessKeyError(
        InvalidAccessKeyError::AccessKeyNotFound {
            account_id: eth_implicit_account_id.clone(),
            public_key: public_key.clone().into(),
        },
    ));
    assert_eq!(response, expected_tx_error);

    // Try to delete ETH-implicit account. Should fail because there is no access key.
    let delete_eth_implicit_account_tx = SignedTransaction::delete_account(
        nonce,
        eth_implicit_account_id.clone(),
        eth_implicit_account_id.clone(),
        "test0".parse().unwrap(),
        &eth_implicit_account_signer,
        *block.hash(),
    );
    let response = env.rpc_handlers[0].process_tx(delete_eth_implicit_account_tx, false, false);
    assert_eq!(response, expected_tx_error);

    // Try to add an access key to the ETH-implicit account. Should fail because there is no access key.
    let add_access_key_to_eth_implicit_account_tx = SignedTransaction::from_actions(
        nonce,
        eth_implicit_account_id.clone(),
        eth_implicit_account_id.clone(),
        &eth_implicit_account_signer,
        vec![Action::AddKey(Box::new(AddKeyAction {
            public_key,
            access_key: AccessKey::full_access(),
        }))],
        *block.hash(),
    );
    let response =
        env.rpc_handlers[0].process_tx(add_access_key_to_eth_implicit_account_tx, false, false);
    assert_eq!(response, expected_tx_error);

    // Try to deploy the Wallet Contract again to the ETH-implicit account. Should fail because there is no access key.
    let magic_bytes = wallet_contract_magic_bytes(&chain_id);
    let wallet_contract_code = wallet_contract(*magic_bytes.hash()).unwrap().code().to_vec();
    let add_access_key_to_eth_implicit_account_tx = SignedTransaction::from_actions(
        nonce,
        eth_implicit_account_id.clone(),
        eth_implicit_account_id,
        &eth_implicit_account_signer,
        vec![Action::DeployContract(DeployContractAction { code: wallet_contract_code })],
        *block.hash(),
    );
    let response =
        env.rpc_handlers[0].process_tx(add_access_key_to_eth_implicit_account_tx, false, false);
    assert_eq!(response, expected_tx_error);
}
```

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L34-78)
```rust
#[tokio::test]
async fn test_insufficient_gas() -> anyhow::Result<()> {
    let TestContext { worker, wallet_contract, wallet_sk, .. } = TestContext::new().await?;

    // If not enough gas is attached to the `rlp_execute` call then the action fails.
    let target = "some.account.near".to_string();
    let action = Action::FunctionCall {
        receiver_id: target.clone(),
        method_name: "greet".into(),
        args: br#"{"name": "Aurora"}"#.to_vec(),
        gas: 5_000_000_000_000,
        yocto_near: 0,
    };
    let signed_transaction = utils::create_signed_transaction(
        0,
        &target.parse().unwrap(),
        Wei::zero(),
        action,
        &wallet_sk,
    );

    let error = wallet_contract
        .inner
        .call(crate::tests::RLP_EXECUTE)
        .args_json(serde_json::json!({
            "target": target,
            "tx_bytes_b64": codec::encode_b64(&codec::rlp_encode(&signed_transaction))
        }))
        .gas(near_gas::NearGas::from_tgas(7))
        .transact()
        .await
        .unwrap()
        .raw_bytes()
        .unwrap_err();

    assert!(
        error.to_string().contains("Exceeded the prepaid gas."),
        "Error should be that there was not enough gas"
    );

    // But the contract is still usable afterwards.
    utils::deploy_and_call_hello(&worker, &wallet_contract, &wallet_sk, 0).await?;

    Ok(())
}
```
