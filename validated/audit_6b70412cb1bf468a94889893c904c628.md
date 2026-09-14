## Analysis

The reported bug class is: a user submits a transaction that references a target by a **mutable identifier** (an ID that can point to different underlying values), and because the resolution of that identifier happens at *execution* time rather than at *submission* time, the actor can end up bound to a different, lower-value/unintended target than they inspected before submitting — for the exact same fee — with no way to pin the expected value.

The same-shaped issue exists in `nearcore`'s **global contract** feature. A user calls `UseGlobalContractAction` with `GlobalContractIdentifier::AccountId(id)` to adopt another account's globally-published WASM code. Unlike `GlobalContractIdentifier::CodeHash(hash)` (immutable, content-addressed), the `AccountId` variant is explicitly documented as mutable: "Contract is deployed under the owner account id. Users will be able reference it by that account id. **This allows the owner to update the contract for all its users.**" [1](#0-0) 

The action itself carries no hash/version to pin the expected code — it only stores the mutable identifier: `pub struct UseGlobalContractAction { pub contract_identifier: GlobalContractIdentifier }` [2](#0-1) . At execution, `use_global_contract` simply checks the identifier key exists in the trie and rewrites the account's `AccountContract` to `GlobalByAccount(id)` [3](#0-2) ; later, `RuntimeContractIdentifier::resolve` fetches whatever code currently lives under that account-id key at call time, not whatever existed when the `UseGlobalContractAction` was signed/submitted [4](#0-3) .

This produces exactly the report's race: a user (Frank) inspects code published under `alice.near` and submits `UseGlobalContractByAccountId { account_id: "alice.near" }` intending to adopt that specific version, paying a fixed fee (`use_global_contract_cost`). Before the transaction is included, the owner (`alice.near`, an ordinary unprivileged account) submits `DeployGlobalContractByAccountId` with new code — this is a plain user action, requiring no elevated privilege, reachable via the exact same `AccountId` deploy mode used in the distribution tests [5](#0-4) . Once the update propagates to the shard (or lands in the same chunk before Frank's `UseGlobalContract` executes), Frank's transaction resolves and binds to the new code for the same fee, with no revert and no way for Frank to express "only bind if code hash == X".

This is functionally identical to the "Eve front-runs Frank" scenario in the report: no slippage/target-pinning control lets a same-cost transaction bind an unsuspecting user to unintended content chosen by another, unprivileged party. Concretely, since `AccountContract::GlobalByAccount` governs which WASM executes for every subsequent `FunctionCall` on the victim's account, a malicious/compromised `alice.near` can advertise a benign contract, wait for adoption via `UseGlobalContractByAccountId`, then redeploy malicious code that silently takes over execution for every account that already bound to it — an invalid/unexpected state transition from the victim's perspective, achieved purely through ordinary transactions.

Note the same unpinned-identifier pattern also appears in `DeterministicAccountStateInitV1.code: GlobalContractIdentifier`, where the `AccountId` form is likewise not hash-pinned [6](#0-5) , meaning a deterministic account created against an `AccountId`-mode global contract inherits whatever code is live at execution, not what was live at signing time.

### Title
Unpinned `AccountId`-mode global contract identifier allows a user's `UseGlobalContract`/`DeterministicStateInit` transaction to silently bind to attacker-controlled code for the same fee - (File: runtime/runtime/src/global_contracts.rs)

### Summary
`UseGlobalContractAction` and `DeterministicStateInitAction` can reference a global contract via `GlobalContractIdentifier::AccountId(id)`, a mutable pointer whose underlying WASM code can be changed at any time by the referenced account owner via `DeployGlobalContractByAccountId`. Neither action lets the caller pin the expected code hash, so the code bound to the caller's account is whatever is live in the trie at the moment the receipt executes, not what was live when the transaction was signed or submitted.

### Finding Description
`use_global_contract` resolves `GlobalContractIdentifier::AccountId(id)` purely by checking key existence and then setting `AccountContract::GlobalByAccount(id)` on the account [3](#0-2) . It never records or checks a code hash, unlike the `CodeHash` variant which is immutable by construction. Later, actual code lookup goes through `RuntimeContractIdentifier::resolve`, which reads the global contract code currently stored for that account id at the time of each function call [4](#0-3) . Because `AccountId`-mode deploys are explicitly designed to be replaced (`DeployGlobalContractDeployMode::AccountId` "allows the owner to update the contract for all its users") [1](#0-0) , any account that has issued (or is in the process of having included) a `UseGlobalContractAction`/`DeterministicStateInitAction` referencing that account id has no way to guarantee which version of the code they end up bound to for the fee they pay — the binding is resolved lazily at execution and receipt-processing time, not at signing time.

### Impact Explanation
A user pays the fixed `use_global_contract`/`deterministic_state_init` fee expecting to adopt a specific, previously-inspected contract implementation. If the owning account redeploys new code under the same `AccountId` identifier between the time the victim signs/broadcasts the transaction and the time it is executed (a normal, unprivileged action reachable by any account), the victim's account silently ends up running different code for the identical price, with no revert path and no on-chain evidence of consent to the new code. Because the bound code fully controls that account's subsequent `FunctionCall` execution (`AccountContract::GlobalByAccount`), this enables an unauthorized change of contract logic on accounts that already adopted a version they trusted — a rug-pull vector that can lead to unauthorized value movement out of those accounts without their explicit re-authorization.

### Likelihood Explanation
Reaching this requires only two ordinary, permissionless transactions: a `DeployGlobalContractAction` with `GlobalContractDeployMode::AccountId` by the identifier owner, and any account's `UseGlobalContractAction`/`DeterministicStateInitAction` referencing that identifier. No validator, node, or protocol privilege is needed, and the race window (submission vs. execution, plus cross-shard distribution delay documented in `docs/RuntimeSpec/Actions.md`: "It may take a while for it to propagate to all shards") [7](#0-6)  gives ample opportunity for the identifier owner to redeploy before a pending `UseGlobalContract` transaction lands.

### Recommendation
Add an opt-in slippage/pinning control: allow `UseGlobalContractAction` (and the `code` field of `DeterministicAccountStateInitV1`) to optionally carry an expected code hash alongside the `AccountId` identifier, and have `use_global_contract`/state-init application verify the currently-stored code hash for that account id matches the expected hash before binding, failing the action (e.g., a new `GlobalContractCodeMismatch` error) otherwise.

### Proof of Concept
1. `alice.near` deploys benign contract `V1` via `DeployGlobalContractAction { deploy_mode: AccountId }`.
2. `bob.near` inspects `V1`, signs `UseGlobalContractAction { contract_identifier: AccountId("alice.near") }`, and broadcasts it.
3. Before `bob.near`'s transaction is included/executed on `bob.near`'s shard, `alice.near` submits `DeployGlobalContractAction { code: V2 (malicious), deploy_mode: AccountId }`, which propagates via `GlobalContractDistributionReceipt` [8](#0-7) .
4. `bob.near`'s `UseGlobalContractAction` executes after `V2` has landed; `use_global_contract` sets `bob.near`'s `AccountContract::GlobalByAccount("alice.near")` with no hash check [9](#0-8) .
5. Subsequent `FunctionCall`s on `bob.near` execute `V2`, not the `V1` code Bob inspected and paid to adopt, confirmed by `RuntimeContractIdentifier::resolve` always reading the live code for the account id [4](#0-3) .

### Citations

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

**File:** core/primitives/src/action/mod.rs (L190-205)
```rust
/// Use global contract action
#[derive(
    BorshSerialize,
    BorshDeserialize,
    serde::Serialize,
    serde::Deserialize,
    PartialEq,
    Eq,
    Clone,
    ProtocolSchema,
    Debug,
)]
#[cfg_attr(feature = "schemars", derive(schemars::JsonSchema))]
pub struct UseGlobalContractAction {
    pub contract_identifier: GlobalContractIdentifier,
}
```

**File:** runtime/runtime/src/global_contracts.rs (L76-107)
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
    account.set_storage_usage(
        account.storage_usage().checked_add(contract_identifier.len() as u64).ok_or_else(|| {
            StorageError::StorageInconsistentState(format!(
                "Storage usage integer overflow for account {}",
                account_id
            ))
        })?,
    );
    account.set_contract(contract).or_inconsistent_state(account_id)?;
```

**File:** runtime/runtime/src/global_contracts.rs (L143-171)
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
    Ok(())
}
```

**File:** runtime/runtime/src/contract_code.rs (L36-50)
```rust
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L268-308)
```rust
/// Test that nonce-based idempotency prevents stale overwrites during global contract updates.
///
/// Deploys a trivial contract first (AccountId mode), waits for distribution,
/// then deploys rs_contract (AccountId mode) with a higher auto-incremented nonce.
/// Verifies all shards have the newer version by calling a function that only
/// exists in the rs_contract.
#[test]
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_global_contract_nonce_prevents_stale_overwrite() {
    init_test_logger();
    let mut env = GlobalContractsReshardingTestEnv::setup();

    let deploy_user = env.users[0].clone();

    // Step 1: Deploy trivial contract as first version (AccountId mode).
    tracing::info!(target: "test", "Deploying first version of global contract (trivial contract)...");
    let tx = env.chunk_producer_node().tx_deploy_global_contract(
        &deploy_user,
        near_test_contracts::trivial_contract().to_vec(),
        GlobalContractDeployMode::AccountId,
    );
    env.env.runner_for_account(&env.chunk_producer).run_tx(tx, Duration::seconds(5));

    // Step 2: Deploy rs_contract as second version (AccountId mode).
    // This will have a higher auto-incremented nonce.
    tracing::info!(target: "test", "Deploying second version of global contract (rs_contract)...");
    let tx = env.chunk_producer_node().tx_deploy_global_contract(
        &deploy_user,
        near_test_contracts::rs_contract().to_vec(),
        GlobalContractDeployMode::AccountId,
    );
    env.env.runner_for_account(&env.chunk_producer).run_tx(tx, Duration::seconds(5));

    // Step 3: Have all users use the global contract and verify that the rs_contract
    // version (v2) is active by calling "log_something" which only exists in rs_contract.
    tracing::info!(target: "test", "Calling use global contract from all users to verify rs_contract is active...");
    for user in &env.users {
        let identifier = GlobalContractIdentifier::AccountId(deploy_user.clone());
        let tx = env.chunk_producer_node().tx_use_global_contract(user, identifier);
        env.env.runner_for_account(&env.chunk_producer).run_tx(tx, Duration::seconds(5));
    }
```

**File:** docs/DataStructures/Account.md (L152-165)
```markdown
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
```

**File:** docs/RuntimeSpec/Actions.md (L484-488)
```markdown
**Execution Error**:

- `GlobalContractDoesNotExist` if the referenced global contract does not exist on the shard of the receiver. (It may
  take a while for it to propagate to all shards.)

```
