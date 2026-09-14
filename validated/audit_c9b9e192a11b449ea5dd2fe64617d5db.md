### Title
Any caller can grief `WalletContract::rlp_execute` and block a legitimate relayer's transaction with a near-zero-cost call - (File: `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`)

### Summary
The NEAR Wallet Contract (deployed as a global contract for every ETH-implicit account, per NEP-696) enforces "only one transaction in flight at a time" via a boolean flag `has_in_flight_tx`. `rlp_execute` is a public, unauthenticated `#[near_bindgen]` method — any account can call it against any victim's eth-implicit account by sending an ordinary `FunctionCall` action, no access key on the target account is required. Setting the flag and rejecting concurrent calls is the same "cheap competing action blocks legitimate progress" pattern as the Tessera `OptimisticListingSeaport` bug: an attacker can occupy the single execution slot with a near-free call, causing a legitimate relayer's simultaneous `rlp_execute` to fail with `"transaction already in progress"`.

### Finding Description
`rlp_execute` checks `self.has_in_flight_tx` and, if set, immediately returns a failure without doing anything else: [1](#0-0) 

Crucially, this method carries no `#[private]` marker and no predecessor check tying the caller to the account owner or an authorized relayer — it is invoked as a plain cross-contract `FunctionCall` where `predecessor_account_id` is simply whoever the sender is: [2](#0-1) 

`has_in_flight_tx` is set to `true` as soon as *any* call (including one that will end up in a relayer-error / ban-relayer promise chain) produces a promise, and is only cleared in the corresponding private callbacks (`address_check_callback`, `nep_141_storage_balance_callback`, `rlp_execute_callback`, `ban_relayer`): [3](#0-2) 

The existing test `test_simultaneous_transactions` already demonstrates the mechanism (two calls in one batch, the second one fails with "transaction already in progress"), but it only exercises the *same* signer submitting two calls, not a third-party attacker targeting a victim account it does not own: [4](#0-3) 

Because `rlp_execute` accepts an arbitrary `tx_bytes_b64`/`target` from *any* predecessor and only fails validation deep inside (`validate_tx_relayer_data`, which is invoked only after the in-flight flag has already been (or is about to be) set for the resulting promise/ban-relayer branch), an attacker with no relationship to the victim account can:
1. Submit a `FunctionCall` to `victim_eth_implicit_account.rlp_execute(target, garbage_tx_bytes)`.
2. This is processed as an ordinary, cheap transaction (or receipt in the same chunk as the victim's legitimate relayer transaction).
3. Whichever call is scheduled/executed first sets `has_in_flight_tx = true`; the other is rejected with `"transaction already in progress"`.

This mirrors the Tessera analog precisely: a minimal-cost, permissionless action occupies a shared, exclusive execution slot and blocks the legitimate operation (the user's relayer submitting the real, fee-paying Ethereum-emulated transaction) from being executed in the same window, and the attacker can repeat this indefinitely each time the wallet is meant to process a legitimate transaction.

### Impact Explanation
This allows censorship/denial-of-service against any ETH-implicit account's transaction processing: a griefer can perpetually prevent a specific wallet's relayer-submitted transactions from succeeding by racing a garbage `rlp_execute` call into the same or an earlier position each time the victim tries to transact, at negligible cost (one small `FunctionCall` worth of gas). While it does not directly move funds, corrupt state, or cause consensus divergence, it is a concrete, reachable-by-any-RPC-caller/transaction-signer availability attack against a core, protocol-blessed component (the wallet contract that underlies ETH-implicit accounts), forcing repeated failed relayer submissions and blocking normal usage of the account — a persistent frozen/degraded-funds-access condition for the affected user until the attacker stops.

### Likelihood Explanation
Likelihood is high: the attack requires no special permission, no access key on the victim account, and no coordination — only knowledge of the victim's eth-implicit account id (which is derived deterministically from a public secp256k1 address) and the ability to submit an ordinary transaction/receipt. It can be repeated every block/chunk essentially for the cost of one cheap `FunctionCall`.

### Recommendation
Restrict `rlp_execute` so that only calls where `predecessor_account_id` is a party authorized to act for the wallet (e.g. the account itself, or an explicitly whitelisted/registered relayer set by the owner) can set/observe `has_in_flight_tx`, or gate the in-flight check on a per-caller/per-relayer basis instead of a single global boolean, so an unrelated third party cannot occupy the execution slot for a victim's account. Alternatively, validate the RLP transaction's signature/target against the wallet's own key *before* allowing any predecessor other than a pre-authorized relayer to influence `has_in_flight_tx`.

### Proof of Concept
1. Deploy the wallet contract as a global contract and create/fund an eth-implicit account `V` (victim), as in `test_wallet_contract_interaction` [5](#0-4) .
2. Have the victim's honest relayer prepare a legitimate `rlp_execute` call on `V` in block `N`.
3. From an unrelated attacker account `A` with no access key on `V`, submit `V.rlp_execute(garbage_target, garbage_tx_bytes_b64)` as a normal `FunctionCall` action targeting `V`, scheduled to land in the same block/chunk (or immediately before the legitimate call resolves — since `has_in_flight_tx` stays `true` across the whole promise chain until a callback runs).
4. Observe: whichever of the two calls executes first sets `has_in_flight_tx = true`; the second — potentially the victim's legitimate relayer transaction — returns `ExecuteResponse { success: false, error: Some("Error: transaction already in progress, please try again later.") }`, exactly as reproduced by the existing `test_simultaneous_transactions` test but now cross-account and attacker-controlled [4](#0-3) .
5. Repeat every time the victim attempts to transact to sustain the denial-of-service.

### Citations

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs (L88-105)
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

**File:** runtime/near-wallet-contract/implementation/wallet-contract/src/tests/sanity.rs (L121-167)
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
```

**File:** integration-tests/src/tests/features/wallet_contract.rs (L227-278)
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
```
