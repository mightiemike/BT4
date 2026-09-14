### Title
Deterministic/Global-by-Account contracts are permanently bound to a mutable code pointer the account owner cannot revoke, enabling an unprivileged global-contract publisher to permanently freeze funds — (File: `runtime/runtime/src/global_contracts.rs`)

### Summary
NEP-616 deterministic accounts (and any account that calls `UseGlobalContractAction`) can bind their executable code to `GlobalContractIdentifier::AccountId(publisher)` rather than an immutable `CodeHash`. That binding is resolved dynamically to *whatever code the publisher account currently has deployed* at call time, and the publisher can redeploy new code under the same identifier at any later point with no restriction. Because a deterministic account never receives a full-access key and its `actor_id` stays pinned to the creating predecessor, the account itself has no way to detach from, or override, that reference. A hostile or compromised publisher can therefore redeploy code that removes all withdrawal/transfer paths (or always fails), permanently bricking every account that references it — an unprivileged-party "freeze authority" over funds that mirrors the SPL mint-freeze-authority bug class described in the report.

### Finding Description
`GlobalContractIdentifier::AccountId(account_id)` is a live pointer, not a content hash. `action_deploy_global_contract` lets the owning account redeploy new code under this identifier repeatedly and without limitation: [1](#0-0) 

Every time an account resolves its contract for execution, the code is fetched fresh from the *current* trie entry for that identifier rather than being pinned at the moment of `UseGlobalContractAction`/`DeterministicStateInitAction`: [2](#0-1) [3](#0-2) 

This is explicitly exercised in the test suite — `test_deploy_global_contract` redeploys the same `AccountId`-mode global contract and shows downstream accounts pick up the new code: [4](#0-3) 

A NEP-616 deterministic account created via `DeterministicStateInitAction` deliberately keeps its `actor_id` pinned to the predecessor "to prevent hijacking the account" — meaning the deterministic account can never issue `AddKey`, `DeployContract`, or a new `UseGlobalContractAction` on itself to escape a bad reference, because it never has a full-access key of its own: [5](#0-4) 

The account's code (and thus every action it can perform, including any transfer of its funds) is therefore permanently outsourced to the discretion of the global-contract publisher — an unrelated, unprivileged account from the perspective of the deterministic account's beneficiary — exactly analogous to an SPL mint's freeze authority being able to freeze a token account irrespective of the holder's wishes.

### Impact Explanation
If the publisher of a `GlobalContractIdentifier::AccountId`-mode contract (maliciously, or because their key is compromised/lost, or simply by upgrading in a breaking way) redeploys code that omits withdrawal/transfer logic or always panics, every account bound to that identifier via `UseGlobalContractAction` or `DeterministicStateInitAction` — none of which can independently switch code — loses all ability to move its balance. This is a permanent, protocol-level fund freeze reachable purely by a `DeployGlobalContractAction` transaction from the publisher, with no on-chain recourse for affected account holders (`DeleteAccount`/`AddKey` are unavailable to a deterministic account since it has no access keys and its actor identity never becomes itself).

### Likelihood Explanation
This requires only two ordinary, unprivileged transactions: (1) some account deploys a global contract in `AccountId` mode and users elect to bind to it (`UseGlobalContractAction`/`DeterministicStateInitAction`, both permissionless, single-signer actions), and (2) the same publisher later submits another `DeployGlobalContractAction` with different code. There is no cooldown, ownership transfer restriction, or immutability guarantee once other accounts have bound to the identifier — the mechanism is fully reachable from a standard signed transaction.

### Recommendation
- Warn users/tooling prominently that binding to `GlobalContractIdentifier::AccountId` grants the publisher perpetual, revocation-proof control over the bound account's executable logic, and prefer `CodeHash`-mode (immutable) global contracts wherever funds custody matters.
- Consider providing deterministic accounts (or any account bound via `UseGlobalContractAction` to an `AccountId` identifier) an explicit, self-authorized mechanism to pin/freeze the currently-resolved code hash or to migrate away from a compromised publisher, rather than leaving them permanently dependent on a mutable, externally-controlled pointer.

### Proof of Concept
1. Account `pub.near` deploys a benign contract in `GlobalContractDeployMode::AccountId` (`action_deploy_global_contract`, `global_contracts.rs:25`).
2. User `alice.near` creates a NEP-616 deterministic account (or calls `UseGlobalContractAction`) referencing `GlobalContractIdentifier::AccountId("pub.near")` and deposits funds into it (`deterministic_account_id.rs:15` / `global_contracts.rs:65`).
3. `pub.near` submits a second `DeployGlobalContractAction` under the same `AccountId` mode, this time with code that contains no transfer/withdraw entry point (or one that always fails) — as shown reachable in `test_deploy_global_contract` (`pytest/tests/contracts/deploy_call_global_smart_contract.py:55-67`).
4. Any subsequent `FunctionCall` to the bound deterministic/global-by-account account now resolves to the new, hostile code (`RuntimeContractIdentifier::resolve`, `contract_code.rs:36-50`), and since the deterministic account has no full-access key and its `actor_id` is pinned to the original predecessor (`deterministic_account_id.rs:117-133`), the funds held by that account are permanently unreachable.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L143-158)
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
```

**File:** runtime/runtime/src/contract_code.rs (L32-50)
```rust
impl RuntimeContractIdentifier {
    /// Resolve a contract identifier from an account's contract field.
    ///
    /// Returns `RuntimeContractIdentifier::None` if the account has no contract deployed.
    pub(crate) fn resolve(
        account_id: &AccountId,
        account_contract: AccountContract,
        state_update: &TrieUpdate,
        chain_id: &str,
        access: AccessOptions,
    ) -> Result<Self, StorageError> {
        let local_hash = match GlobalContractIdentifier::try_from(account_contract) {
            Ok(gci) => {
                let code_hash = gci.clone().hash(state_update, access)?;
                return Ok(RuntimeContractIdentifier::Global { code_hash, identifier: gci });
            }
            Err(ContractIsLocalError::NotDeployed) => return Ok(RuntimeContractIdentifier::None),
            Err(ContractIsLocalError::Deployed(local_hash)) => local_hash,
        };
```

**File:** runtime/runtime/src/contract_code.rs (L91-117)
```rust
impl GlobalContractAccessExt for GlobalContractIdentifier {
    fn hash(self, store: &TrieUpdate, access: AccessOptions) -> Result<CryptoHash, StorageError> {
        if let GlobalContractIdentifier::CodeHash(hash) = self {
            return Ok(hash);
        }
        let key = TrieKey::GlobalContractCode { identifier: self.into() };
        let value_ref =
            store.get_ref(&key, KeyLookupMode::MemOrFlatOrTrie, access)?.ok_or_else(|| {
                let TrieKey::GlobalContractCode { identifier } = key else { unreachable!() };
                StorageError::StorageInconsistentState(format!(
                    "Global contract identifier not found {:?}",
                    identifier
                ))
            })?;
        Ok(value_ref.value_hash())
    }

    fn code(self, store: &TrieUpdate) -> Result<Option<ContractCode>, StorageError> {
        let key = TrieKey::GlobalContractCode { identifier: self.clone().into() };
        let code_hash = match self {
            GlobalContractIdentifier::AccountId(_) => None,
            GlobalContractIdentifier::CodeHash(hash) => Some(hash),
        };
        let code = store.get(&key, AccessOptions::DEFAULT)?;
        Ok(code.map(|code| ContractCode::new(code, code_hash)))
    }
}
```

**File:** pytest/tests/contracts/deploy_call_global_smart_contract.py (L55-67)
```python
    # Redeploy global contract using AccountId method
    deploy_mode = GlobalContractDeployMode()
    deploy_mode.enum = 'accountId'
    deploy_mode.accountId = ()
    deploy_global_contract(rpc, nodes[0], test_contract, deploy_mode, 50)

    identifier = GlobalContractIdentifier()
    identifier.enum = "accountId"
    identifier.accountId = nodes[0].signer_key.account_id
    use_global_contract(rpc, nodes[1], identifier, 60)

    call_contract(rpc, nodes[0], nodes[1].signer_key.account_id, 70)
    call_contract(rpc, nodes[1], nodes[1].signer_key.account_id, 80)
```

**File:** runtime/runtime/src/deterministic_account_id.rs (L117-133)
```rust
pub(crate) fn create_deterministic_account(
    initial_balance: Balance,
    storage_usage_config: &StorageUsageConfig,
) -> Account {
    // Unlike `CreateAccount`, this account creation does not change
    // actor_id. This is important to prevent hijacking the account.
    // Actor id remains the predecessor, so any actions following will
    // be checked against that for actor permissions, preventing
    // `AddKey`, `DeployContract`, or any other actions that only the
    // account owner is permitted to do.
    Account::new(
        initial_balance,
        Balance::ZERO,
        AccountContract::None,
        storage_usage_config.num_bytes_account,
    )
}
```
