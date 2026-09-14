I have sufficient evidence to write the final answer now.

### Title
Deterministic (NEP-616) account identity relies on a mutable `AccountId`-referenced global contract, letting the code owner silently rewrite the "fixed" code of every already-created deterministic account that used `GlobalContractIdentifier::AccountId` - (File: `runtime/runtime/src/global_contracts.rs`, `runtime/runtime/src/deterministic_account_id.rs`)

### Summary
The SKALE finding is that an `ERC721OnChain` *token owner* can rewrite `tokenURI` after the fact, letting them spoof the metadata that other parties trust to describe that specific, already-minted token. The structurally equivalent pattern in nearcore is `GlobalContractDeployMode::AccountId`: NEP‑616 deterministic accounts are documented and designed to have "fixed code and fixed initial state" [1](#0-0)  derived purely from the hash of their `DeterministicAccountStateInit`, but when that state-init's `code` field is a `GlobalContractIdentifier::AccountId` rather than `CodeHash`, the referenced code is *not* pinned by the account's identity — it is a live pointer that the global-contract-owning account can overwrite at any time via a new `DeployGlobalContractAction`, changing the actual executable behavior of every already-created deterministic account referencing it, without changing the account ID that users/contracts derived and trusted.

### Finding Description
`GlobalContractDeployMode` explicitly documents the two modes and their trust implications: `CodeHash` "effectively makes the contract immutable," whereas `AccountId` "allows the owner to update the contract for all its users." [2](#0-1)  The `use_global_contract` runtime function resolves `GlobalContractIdentifier::AccountId(id)` into `AccountContract::GlobalByAccount(id)` on the target account, meaning the account's runtime code is looked up indirectly through the publisher's account ID at call time, not baked into the account's state at creation time. [3](#0-2) 

Deterministic account creation (`action_deterministic_state_init` / `deploy_deterministic_account`) calls exactly this `use_global_contract` path when initializing the account's contract from `state_init.code()`. [4](#0-3)  The deterministic account ID itself is derived only from the borsh-encoded `DeterministicAccountStateInit` (which contains the `GlobalContractIdentifier`, not the actual WASM bytes), per the documented `0s + keccak256(...)` scheme. [5](#0-4) 

Consequently, if a deterministic account is created with `code: GlobalContractIdentifier::AccountId(publisher)`, its ID commits to "code published by `publisher`" — not to any specific bytecode. The publisher can later redeploy different code under `AccountId` mode (a normal `DeployGlobalContractAction` with `deploy_mode: AccountId`, reachable by any account that funds the storage cost for its own account), and this new code becomes what every deterministic account referencing that publisher executes going forward. The distribution/nonce mechanism (`apply_distribution_current_shard`, `increment_nonce`) is explicitly built to let newer deployments overwrite older ones across shards, confirming this is a live, mutable binding rather than a one-time pin. [6](#0-5)  This is exercised directly in-repo by `test_global_contract_update`, which deploys a trivial contract under `AccountId`, has consumer accounts bind to it, then redeploys different code under the same `AccountId` and shows the consumer accounts' behavior change without any action from the consumers. [7](#0-6) 

The test suite even documents that NEP-616 contracts are meant to rely on "predecessor is owner"/"peer" identity checks derived from the deterministic account ID (`test_sharded_contract_owner_check`, `root_check`/`peer_check` in the sharded test contract), which implicitly assumes the code behind that ID is fixed/trustworthy — an assumption `AccountId` mode breaks. [8](#0-7) [9](#0-8) 

### Impact Explanation
Any user, dApp, or contract that creates or interacts with a deterministic account whose `code` uses `GlobalContractIdentifier::AccountId` is trusting that the publisher will never change the code. Since deterministic accounts can hold balances (deposited via the `deposit` field of `DeterministicStateInitAction`, or via subsequent transfers) and their address is used by other contracts as a security anchor (e.g. "predecessor is my deterministic peer" checks), a publisher who redeploys malicious code can:
- Backdoor the logic of every deterministic account bound to that `AccountId` to drain any NEAR balance or contract-controlled assets held on those accounts.
- Break "owner"/"peer" identity assumptions relied upon by other sharded contracts that call into or trust these deterministic accounts, enabling unauthorized value movement across accounts that believed they were talking to fixed, known code.

This is a concrete unauthorized value-movement vector, directly analogous to the ERC721 owner rewriting `tokenURI` to spoof what other parties believe is immutable, verifiable data tied to an identifier.

### Likelihood Explanation
No special privilege is required beyond being the original publisher of the `AccountId`-mode global contract (any account can create one). The redeploy path (`DeployGlobalContractAction` with `deploy_mode: AccountId`) is a completely ordinary, permissionless action available to any transaction signer, and is explicitly supported/tested behavior (`test_global_contract_update`, `test_global_contract_nonce_prevents_stale_overwrite`). Because the mechanism is intentional (documented behavior for the `AccountId` mode), likelihood of a malicious or later-compromised publisher exploiting downstream trust is realistic wherever developers choose `AccountId` mode for deterministic-account code without clearly communicating the mutability risk to end users, mirroring exactly the SKALE situation where the disputed-but-accepted-as-Medium behavior stemmed from a documented but under-appreciated admin/owner privilege.

### Recommendation
- For any deterministic account whose expected security model depends on fixed code (e.g., the "owner"/"peer" identity checks demonstrated in the sharded test contract), require or strongly recommend `GlobalContractIdentifier::CodeHash` rather than `AccountId` at the protocol or tooling level, since only `CodeHash` gives the immutability guarantee the NEP‑616 "fixed code" description implies.
- Consider surfacing a protocol-level warning/lint (e.g., in the RPC/CLI tooling that constructs `DeterministicAccountStateInit`) when `AccountId` mode is used, since the resulting account ID does not actually commit to executable bytecode, only to a mutable reference.
- Document explicitly in `docs/DataStructures/Account.md`'s deterministic-account section that "fixed code" only holds under `CodeHash` deploy mode, cross-referencing the existing `GlobalContractDeployMode` warning already present in `core/primitives/src/action/mod.rs`.

### Proof of Concept
1. Account `P` deploys global contract `A` (benign) with `DeployGlobalContractAction { deploy_mode: AccountId }`.
2. User `U` derives and creates a deterministic account `D` with `DeterministicAccountStateInit { code: GlobalContractIdentifier::AccountId(P), data }`, funds it with NEAR, and other contracts start trusting `D`'s "owner"/"peer" identity per NEP-616 patterns (as in `test_sharded_contract_owner_check`).
3. `P` later submits a second `DeployGlobalContractAction { deploy_mode: AccountId }` with malicious code (e.g., one that transfers out balances or forges "owner" checks) — exactly the flow exercised by `test_global_contract_update`. [10](#0-9) 
4. Without any action by `U` or `D`, subsequent calls into `D` now execute `P`'s new, malicious code, since `D`'s `AccountContract::GlobalByAccount(P)` resolves to whatever is currently stored under `P`'s global contract slot. [11](#0-10) 
5. Funds/assets on `D`, or contracts trusting `D`'s identity, are now controlled by `P`'s new code.

### Citations

**File:** docs/DataStructures/Account.md (L138-142)
```markdown
## Deterministic accounts

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

**File:** core/primitives/src/action/mod.rs (L135-144)
```rust
pub enum GlobalContractDeployMode {
    /// Contract is deployed under its code hash.
    /// Users will be able reference it by that hash.
    /// This effectively makes the contract immutable.
    CodeHash,
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
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

**File:** runtime/runtime/src/global_contracts.rs (L173-227)
```rust
/// Increments the nonce for the given global contract identifier and writes
/// it to state immediately.
fn increment_nonce(
    state_update: &mut TrieUpdate,
    id: &GlobalContractIdentifier,
) -> Result<u64, RuntimeError> {
    let identifier: GlobalContractCodeIdentifier = id.clone().into();

    let nonce_key = TrieKey::GlobalContractNonce { identifier };
    let stored_nonce = get_nonce(state_update, &nonce_key)?;

    let new_nonce = stored_nonce.checked_add(1).ok_or_else(|| {
        RuntimeError::UnexpectedIntegerOverflow("increment_global_contract_nonce".into())
    })?;
    set_nonce(state_update, nonce_key, new_nonce);
    Ok(new_nonce)
}

fn apply_distribution_current_shard(
    receipt: &Receipt,
    global_contract_data: &GlobalContractDistributionReceipt,
    apply_state: &ApplyState,
    state_update: &mut TrieUpdate,
) -> Result<Compute, RuntimeError> {
    let identifier = match &global_contract_data.id() {
        GlobalContractIdentifier::CodeHash(hash) => GlobalContractCodeIdentifier::CodeHash(*hash),
        GlobalContractIdentifier::AccountId(account_id) => {
            GlobalContractCodeIdentifier::AccountId(account_id.clone())
        }
    };

    let is_nonce_fresh = check_and_update_nonce(global_contract_data, &identifier, state_update)?;
    if !is_nonce_fresh {
        return Ok(0);
    }

    let config = apply_state.config.wasm_config.clone();
    let trie_key = TrieKey::GlobalContractCode { identifier };
    let code_len = global_contract_data.code().len() as u64;
    state_update.set(trie_key, global_contract_data.code().to_vec());

    // Record the deploy so a same-chunk call can find the code without a warm cache.
    let code_hash = match global_contract_data.id() {
        GlobalContractIdentifier::CodeHash(hash) => Some(*hash),
        GlobalContractIdentifier::AccountId(_) => None,
    };
    if ProtocolFeature::GlobalContractSameChunkCallFix.enabled(apply_state.current_protocol_version)
    {
        state_update.record_global_contract_deploy(ContractCode::new(
            global_contract_data.code().to_vec(),
            code_hash,
        ));
    }

    state_update.commit(StateChangeCause::ReceiptProcessing { receipt_hash: receipt.get_hash() });
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

**File:** test-loop-tests/src/tests/global_contracts.rs (L72-106)
```rust
fn test_global_contract_update() {
    let mut env = GlobalContractsTestEnv::setup(Balance::from_near(1000));
    let use_accounts = [env.account_shard_0.clone(), env.account_shard_1.clone()];

    env.deploy_trivial_global_contract(GlobalContractDeployMode::AccountId);

    for account in &use_accounts {
        env.use_global_contract(
            account,
            GlobalContractIdentifier::AccountId(env.deploy_account.clone()),
        );

        // Currently deployed trivial contract doesn't have any methods,
        // so we expect any function call to fail with MethodNotFound error
        let call_tx = env.call_global_contract_tx(account.clone(), account.clone());
        let call_outcome = env.execute_tx(call_tx);
        assert_matches!(
            call_outcome.status,
            FinalExecutionStatus::Failure(TxExecutionError::ActionError(ActionError {
                kind: ActionErrorKind::FunctionCallError(FunctionCallError::MethodResolveError(
                    MethodResolveError::MethodNotFound
                )),
                index: _
            }))
        );
    }

    env.deploy_global_contract(GlobalContractDeployMode::AccountId);

    for account in &use_accounts {
        // Function call should be successful after deploying rs contract
        // containing the function we call here
        env.assert_call_global_contract_success(account.clone(), account.clone());
    }
}
```

**File:** test-loop-tests/src/tests/deterministic_account_id.rs (L738-741)
```rust
/// Deploy a sharded toy-contract and check it can do a "predecessor is owner"
/// check as intended by NEP-616.
#[test]
fn test_sharded_contract_owner_check() {
```

**File:** runtime/near-test-contracts/sharded-contract/src/lib.rs (L212-223)
```rust
/// Check that the predecessor is the owner of this sharded contract instance.
unsafe fn root_check() {
    storage_read(OWNER_KEY.len() as u64, OWNER_KEY.as_ptr() as u64, REG_A);
    predecessor_account_id(REG_B);

    if !registers_eq(REG_A, REG_B) {
        let expected_predecessor = bytes_to_string(register_to_memory(REG_A));
        let predecessor = bytes_to_string(register_to_memory(REG_B));

        panic(&format!("not root: {expected_predecessor} != {predecessor}"));
    }
}
```
