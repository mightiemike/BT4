This confirms the vulnerability class exists in production code and is nowhere mitigated against the specific attack of the code identifier being mutable at deploy time. All test coverage (`test_repeated_deterministic_state_init`, `check_deterministic_state_init`, etc.) exercises the happy path with a *stable* global contract, never the scenario where the global contract owner changes the code between the moment the deterministic id is computed off-chain and the moment `DeterministicStateInitAction` actually executes.

### Title
Deterministic account ID commits only to a mutable `GlobalContractIdentifier::AccountId` reference, not to the actual code, letting the global-contract owner bait-and-switch a depositor's `DeterministicStateInitAction` - (File: `runtime/runtime/src/deterministic_account_id.rs`)

### Summary
NEP-616 deterministic accounts derive their account ID by hashing a `DeterministicAccountStateInit` value [1](#0-0) , whose `code` field can be a `GlobalContractIdentifier::AccountId(owner)` rather than a fixed code hash [2](#0-1) . Because the hash only commits to the *identifier* (the owner's account id string), and `GlobalContractDeployMode::AccountId` explicitly allows that owner to redeploy different code at any time [3](#0-2) , the resolved code that actually gets baked into the deterministic account at execution time can differ from whatever code the depositor observed when they computed the account id and decided to fund it. This mirrors the Maverick `Router.getOrCreatePoolAndAddLiquidity` bug: the "identity" (pool address / account id) is fixed and appears safe to commit to, but a party who controls a mutable piece of the referenced state (initial active tick / contract code) can change it between commitment and execution, causing the depositor's attached funds to be governed by unintended logic.

### Finding Description
`action_deterministic_state_init` only deploys code+data the *first* time the account transitions from `uninit` to `active` (get-or-create semantics, "if the account was already created before: do nothing") [4](#0-3) [5](#0-4) . When it does deploy, `deploy_deterministic_account` calls `use_global_contract(state_update, account_id, account, state_init.code(), result)`, which resolves the identifier against *whatever code is currently stored* under that identifier in the trie at execution time, not at the time the depositor derived the address [6](#0-5) [7](#0-6) .

The account id is derived purely from the borsh-encoding of `DeterministicAccountStateInitV1 { code: GlobalContractIdentifier, data }` [8](#0-7) . When `code = GlobalContractIdentifier::AccountId(owner)`, the hash commits only to `owner`'s account id string, not to any code hash — exactly analogous to Maverick's pool being identified by a token pair while its actual initial price (active tick) is a separate, attacker-settable value not captured by the pool's "identity." A user (Alice) can:
1. Observe `owner.near`'s currently-deployed global contract, verify it is benign, and derive `det_account = derive({code: AccountId(owner.near), data: D})`.
2. Submit a `Transfer` (to fund storage) and/or `DeterministicStateInitAction` with a `deposit` to `det_account`, believing the resulting account will run the benign code she inspected.
3. Before her transaction executes (front-run in the same or an earlier block, since `owner.near` can redeploy at will and the account only "locks in" code on first init), `owner.near` deploys new, malicious `AccountId`-mode global contract code.
4. Alice's `DeterministicStateInitAction` executes, calling `use_global_contract` with the *same* `GlobalContractIdentifier::AccountId(owner.near)`, but now resolving to the malicious code, which is set as `det_account`'s permanent contract (`account.contract().is_none()` was true, so this is the one-shot deploy that can never be repeated) [9](#0-8) .
5. Alice's `deposit` (and any prior `Transfer`) now sits on an account running attacker-chosen code, which can call back and drain the balance via a `FunctionCall`, exactly as Maverick's attacker could swap against the manipulated pool price to steal the first depositor's funds.

Documentation frames the "code identity change" hazard only in terms of same-chunk pipelining ordering, not this cross-time bait-and-switch: `pipelining.rs` explicitly blocks preparation of `FunctionCall`s in the same chunk after a `DeterministicStateInit`/`UseGlobalContract` because "a later function call could be prepared against the account's current contract and then executed under a freshly created or recreated account with different (or no) code" [10](#0-9)  — this guards intra-chunk consistency, but does nothing to stop the owner from changing the referenced global contract's code in a *prior* chunk/block, which is the actual attack window here.

### Impact Explanation
Any account can act as the "malicious owner": deploy an innocuous `AccountId`-mode global contract, wait for depositors to derive deterministic accounts against it and attach funds via `Transfer`/`DeterministicStateInitAction`, then redeploy malicious code before the depositor's state-init transaction lands, permanently installing attacker-controlled logic on the depositor's own funded account and enabling the attacker to drain the attached deposit — concrete unauthorized value movement reachable from a single unprivileged transaction, with no validator or node compromise required.

### Likelihood Explanation
This requires no special privileges: deploying an `AccountId`-mode global contract and later redeploying it is a fully permissionless, expected operation [3](#0-2) . The only precondition is that a victim chooses to trust and reference someone else's mutable global contract by account id when constructing a deterministic account with an attached deposit — a usage pattern the protocol explicitly supports and even documents test coverage for (`GlobalContractDeployMode::AccountId` deterministic accounts) [11](#0-10) , but for which no existing test exercises the redeploy-before-first-init race.

### Recommendation
For `DeterministicAccountStateInitV1.code`, require (or strongly recommend/enforce) `GlobalContractIdentifier::CodeHash` rather than `AccountId` whenever a deposit/value transfer accompanies state init, or alternatively pin the *resolved* code hash into the state-init commitment (so the derived account id changes if the owner's code changes), so that the deterministic ID's promise of "fixed code and fixed initial state" [12](#0-11)  actually holds when `AccountId`-mode identifiers are used.

### Proof of Concept
1. Attacker deploys a trivial, benign global contract in `AccountId` mode as `owner.near` [13](#0-12) .
2. Victim derives `state_init = V1{code: AccountId(owner.near), data: D}`, computes `det_account = derive(state_init)`, and submits `Transfer` + `DeterministicStateInitAction{state_init, deposit}` to `det_account`.
3. Before victim's transaction is applied, attacker submits a new `DeployGlobalContractAction{code: malicious_code, deploy_mode: AccountId}` as `owner.near`, overwriting the global contract code under the same `AccountId` identifier (see nonce-nonce nonotency semantics in `global_contracts.rs`'s `initiate_distribution`/`check_and_update_nonce`) [14](#0-13) .
4. Victim's `DeterministicStateInitAction` executes: `account.contract().is_none()` is true, so `deploy_deterministic_account` runs and calls `use_global_contract` with `contract_identifier = AccountId(owner.near)`, which now resolves to the malicious code [6](#0-5) .
5. `det_account` is now permanently initialized (one-shot; future `DeterministicStateInitAction`s with the same `state_init` are no-ops per `test_repeated_deterministic_state_init` [15](#0-14) ) with attacker-chosen code, and the victim's `deposit`/prior transfer sit on an account the attacker fully controls logically, which the attacker can call to withdraw or misuse.

### Citations

**File:** docs/DataStructures/Account.md (L140-142)
```markdown
Deterministic accounts are an advanced kind of implicit account.
A normal implicit account has a fixed access key that is implicitly associated with it.
Deterministic accounts have a fixed code and fixed initial state associated with it.
```

**File:** docs/DataStructures/Account.md (L151-172)
```markdown
```rust
pub enum DeterministicAccountStateInit {
    V1(DeterministicAccountStateInitV1),
}

pub struct DeterministicAccountStateInitV1 {
    pub code: GlobalContractIdentifier,
    pub data: BTreeMap<Vec<u8>, Vec<u8>>,
}

pub enum GlobalContractIdentifier {
    CodeHash(CryptoHash) = 0,
    AccountId(AccountId) = 1,
}
```

### Deterministic account ID

The account ID is derived from a `DeterministicAccountStateInit` instance.

To derive the deterministic account id, borsh-encode the `DeterministicAccountStateInit` enum instance into raw
bytes. Then use the following formula: `'0s' + keccak256(bytes)[12:32].hex()`.
```

**File:** core/primitives-core/src/deterministic_account_id.rs (L39-44)
```rust
pub struct DeterministicAccountStateInitV1 {
    pub code: GlobalContractIdentifier,
    #[serde_as(as = "BTreeMap<Base64, Base64>")]
    #[cfg_attr(feature = "schemars", schemars(with = "BTreeMap<String, String>"))]
    pub data: BTreeMap<Vec<u8>, Vec<u8>>,
}
```

**File:** core/primitives/src/action/mod.rs (L140-143)
```rust
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
```

**File:** docs/RuntimeSpec/Actions.md (L500-507)
```markdown
**Outcome**:

- if the account was already created before:
    - do nothing
- if the account was not created before:
    - creates an account with deterministic account id
    - sets the contract code to the specified global contract
    - stores the initial data into the contract storage
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L27-52)
```rust
    let account = match maybe_account {
        Some(account) => account,
        None => {
            // cspell:ignore nonexist
            // `nonexist` -> `uninit` account state transition
            // Create with zero balance now and check later how much of the
            // provided deposit is needed.
            let new_account = create_deterministic_account(Balance::ZERO, storage_usage_config);
            maybe_account.insert(new_account)
        }
    };
    if account.contract().is_none() {
        // `uninit` -> `active` account state transition. "uninit" here is the
        // NEP-616 sense, a deterministic account with no contract yet, not
        // `Account::Uninitialized`: a `0u` id can never reach this, because
        // `validate_deterministic_state_init` pins the receiver to the derived
        // `0s` id.
        deploy_deterministic_account(
            state_update,
            account,
            account_id,
            &action.state_init,
            result,
            storage_usage_config,
        )?;
    }
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L142-154)
```rust
fn deploy_deterministic_account(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    state_init: &DeterministicAccountStateInit,
    result: &mut ActionResult,
    storage_usage_config: &StorageUsageConfig,
) -> Result<(), RuntimeError> {
    // Step 1: set contract code (includes storage usage accounting)
    use_global_contract(state_update, account_id, account, state_init.code(), result)?;
    if result.result.is_err() {
        return Ok(());
    }
```

**File:** runtime/runtime/src/global_contracts.rs (L76-98)
```rust
pub(crate) fn use_global_contract(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
    account: &mut Account,
    contract_identifier: &GlobalContractIdentifier,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let key = TrieKey::GlobalContractCode { identifier: contract_identifier.clone().into() };
    if !state_update.contains_key(&key, AccessOptions::DEFAULT)? {
        result.result = Err(ActionErrorKind::GlobalContractDoesNotExist {
            identifier: contract_identifier.clone(),
        }
        .into());
        return Ok(());
    }
    clear_account_contract_storage_usage(state_update, account_id, account)?;
    if account.contract().is_local() {
        state_update.remove(TrieKey::ContractCode { account_id: account_id.clone() });
    }
    let contract = match contract_identifier {
        GlobalContractIdentifier::CodeHash(code_hash) => AccountContract::Global(*code_hash),
        GlobalContractIdentifier::AccountId(id) => AccountContract::GlobalByAccount(id.clone()),
    };
```

**File:** runtime/runtime/src/global_contracts.rs (L143-169)
```rust
fn initiate_distribution(
    state_update: &mut TrieUpdate,
    account_id: AccountId,
    contract_code: Arc<[u8]>,
    deploy_mode: &GlobalContractDeployMode,
    current_shard_id: ShardId,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
    // Increment the nonce and write it to state immediately to prevent multiple
    // distributions with the same nonce from being initiated. This requires
    // allowing the same nonce in the freshness check when applying the
    // distribution receipt.
    let nonce = increment_nonce(state_update, &id)?;
    let distribution_receipt =
        GlobalContractDistributionReceipt::new(id, current_shard_id, vec![], contract_code, nonce);
    let distribution_receipts =
        Receipt::new_global_contract_distribution(account_id, distribution_receipt);
    // No need to set receipt_id here, it will be generated as part of apply_action_receipt
    result.new_receipts.push(distribution_receipts);
```

**File:** runtime/runtime/src/pipelining.rs (L173-192)
```rust
        for (action_index, action) in actions.iter().enumerate() {
            let account_id = account_id.clone();
            match action {
                Action::DeployContract(_)
                | Action::UseGlobalContract(_)
                | Action::DeterministicStateInit(_)
                | Action::UniversalStateInit(_)
                | Action::CreateAccount(_)
                | Action::DeleteAccount(_) => {
                    // Any action that can change the account's executable-code identity within
                    // this chunk must block preparation for the receiver. Otherwise a later
                    // function call could be prepared against the account's current contract
                    // and then executed under a freshly created or recreated account with
                    // different (or no) code.
                    //
                    // FIXME: instead of blocking these accounts, move the handling of
                    // code-identity-changing actions into here, so that the necessary data
                    // dependencies can be established.
                    return self.block_accounts.insert(account_id);
                }
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L291-331)
```rust
#[test]
fn test_repeated_deterministic_state_init() {
    let mut env = TestEnv::setup(Balance::from_near(100));
    env.deploy_global_contract(GlobalContractDeployMode::AccountId);

    let data = BTreeMap::from_iter([(b"key".to_vec(), vec![0u8; 100_000])]);
    let (state_init, det_account) = env.new_deterministic_account_with_data(data.clone());

    // send 10 times the required amount
    let required_for_storage = env.balance_for_storage(state_init);
    let attached_balance = required_for_storage.checked_mul(10).unwrap();

    let deposit_before = env.get_account_state(env.user_account()).amount;

    // first init
    let outcome = env.try_deploy_deterministic_account_with_data(data.clone(), attached_balance);
    outcome.expect("should be able to send transaction").assert_success();
    assert_eq!(
        required_for_storage,
        env.get_account_state(det_account.clone()).amount,
        "exactly required balance should be on the created account"
    );
    let deposit_between = env.get_account_state(env.user_account()).amount;
    assert!(
        deposit_between <= deposit_before.checked_sub(required_for_storage).unwrap(),
        "signer should have been charged the deposit cost + gas cost"
    );

    // second init
    let outcome = env.try_deploy_deterministic_account_with_data(data, attached_balance);
    outcome.expect("should be able to send transaction").assert_success();
    assert_eq!(
        required_for_storage,
        env.get_account_state(det_account).amount,
        "exactly attached balance should be on created account, nothing added on the second call"
    );
    let deposit_after = env.get_account_state(env.user_account()).amount;
    assert!(
        deposit_after > deposit_between.checked_sub(required_for_storage).unwrap(),
        "signer should have been refunded the deposit cost and only spend gas cost on the second call"
    );
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L1159-1170)
```rust
    /// Assumes to use global_contract_account by account id as code.
    fn new_deterministic_account_with_data(
        &self,
        data: BTreeMap<Vec<u8>, Vec<u8>>,
    ) -> (DeterministicAccountStateInit, AccountId) {
        let state_init = DeterministicAccountStateInit::V1(DeterministicAccountStateInitV1 {
            code: GlobalContractIdentifier::AccountId(self.global_contract_account()),
            data,
        });
        let det_account = derive_near_deterministic_account_id(&state_init);
        (state_init, det_account)
    }
```

**File:** test-loop-tests/src/tests/global_contracts.rs (L269-281)
```rust
    fn deploy_global_contract_tx(
        &mut self,
        deploy_mode: GlobalContractDeployMode,
    ) -> SignedTransaction {
        self.deploy_global_contract_custom_tx(deploy_mode, self.contract.code().to_vec())
    }

    fn deploy_global_contract(&mut self, deploy_mode: GlobalContractDeployMode) -> CryptoHash {
        let tx = self.deploy_global_contract_tx(deploy_mode);
        let tx_hash = tx.get_hash();
        self.run_tx(tx);
        tx_hash
    }
```
