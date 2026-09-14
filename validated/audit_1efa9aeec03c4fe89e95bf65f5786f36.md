### Title
Global Contract owner can front-run a pending `FunctionCall` transaction by redeploying (rug-pulling) the `AccountId`-referenced contract code before it executes - (File: `runtime/runtime/src/contract_code.rs`)

### Summary
NEAR's global contracts feature (`DeployGlobalContractAction` / `UseGlobalContractAction`) lets an account reference shared contract code either immutably by hash (`GlobalContractDeployMode::CodeHash`) or mutably by the deployer's account id (`GlobalContractDeployMode::AccountId`). For the `AccountId` mode, the code that will actually run for *every* account that has opted into `AccountContract::GlobalByAccount(id)` is resolved fresh, from current trie state, at the moment each `FunctionCall` receipt is applied — not at the moment the caller's transaction was signed or submitted. This mirrors the Cooler bug class: an actor with unilateral write access to shared "terms" (there: `loan.request.interest`; here: the global contract's WASM code) can redeem-frontrun a victim's in-flight transaction by rewriting those terms immediately before the victim's transaction executes, so the transaction runs under attacker-chosen logic the victim never agreed to, with no way for the victim to pin or bound the expected outcome.

### Finding Description
`GlobalContractDeployMode::AccountId` is documented as intentionally mutable: "Contract is deployed under the owner account id. Users will be able reference it by that account id. This allows the owner to update the contract for all its users" [1](#0-0) , also documented in `docs/RuntimeSpec/Actions.md` [2](#0-1) .

A regular account can adopt this shared code via `UseGlobalContractAction`, which stores only the identifier (`AccountContract::GlobalByAccount(id)`), not a pinned code hash, in the account's contract field [3](#0-2) .

Whenever a `FunctionCall` receipt targets that account, the runtime resolves the actually-executed code by looking up the *current* value stored under the `GlobalContractCode` trie key for that account id, at the exact moment of execution: [4](#0-3) [5](#0-4) 

This resolution happens inside the main action-apply loop for every `FunctionCall`, i.e. per-receipt, using whatever code is live in state at that time: [6](#0-5) 

Redeploying new code under the same `AccountId` identifier is unrestricted for the owner and only serialized by an internal distribution nonce that exists purely to keep shards eventually consistent with each other — it provides no protection for a user who is relying on the *currently observed* code when they craft and submit a transaction: [7](#0-6) 

Put together, a malicious global-contract owner Bob can:
1. Deploy an innocuous/expected contract under `AccountId` mode.
2. Wait for a victim Alice, who has adopted Bob's contract via `UseGlobalContractAction`, to broadcast a `FunctionCall` transaction whose outcome she expects based on the contract code she inspected (e.g. a withdrawal/transfer method).
3. Front-run Alice's transaction with his own `DeployGlobalContractAction(AccountId)` that overwrites the shared code with malicious logic (e.g. one that redirects a transfer to Bob, or drains Alice's account balance on the next call).
4. Alice's already-signed transaction now executes against the new, attacker-chosen logic, because resolution happens at apply time from live trie state, not at signing time.

This is functionally identical to the Cooler exploit: the victim's pending transaction is evaluated against mutable state that the counterparty controls and can rewrite in the same block window, and the victim has no on-chain mechanism (equivalent to a "minDecollateralized" slippage guard) to bound what code/logic her transaction will actually run against.

### Impact Explanation
Because NEAR account code fully controls that account's balance, storage, and any assets/allowances the account exposes to callers, a redeployed malicious global contract can redirect balances, drain state, or otherwise cause unauthorized value movement out of every account that references it via `GlobalByAccount`, exactly at the moment a legitimate transaction was expected to run under the old, audited code. Because `UseGlobalContractAction` is explicitly promoted as a storage-cost optimization for users who want to share code, and nothing in the protocol prevents or even signals to a caller that code changed between transaction construction and execution, this is a High-severity trust violation reachable purely through normal, permissionless transaction submission (`DeployGlobalContractAction`, `UseGlobalContractAction`, `FunctionCall`).

### Likelihood Explanation
Likelihood is high for any account/dApp that chooses `GlobalByAccount` code sharing from a third party (a pattern the protocol explicitly supports to reduce storage costs) without independently re-verifying code immediately before every call. The attack requires only an unprivileged transaction signer (the global-contract owner) racing a normal mempool transaction — no validator/network privilege, no protocol bug beyond the intended-but-dangerous mutability semantics of `AccountId` deploy mode.

### Recommendation
- For security-sensitive integrations, prefer/require `GlobalContractDeployMode::CodeHash` (immutable), as NEAR's own built-in ETH-implicit wallet contract already does by using a fixed `eth_wallet_global_contract_hash` rather than the mutable `AccountId` form [8](#0-7) .
- Consider adding an optional "expected code hash" pin to `UseGlobalContractAction`/`FunctionCall` so a caller (or the account's own stored reference) can require execution to abort if the resolved global contract code hash differs from what was expected at call time, closing the slippage-style gap that lets the owner rewrite semantics between signing and execution.
- At minimum, strengthen documentation/tooling warnings that `AccountId`-mode global contracts are fully and instantly mutable by their owner and thus unsuitable for any use case where callers rely on a specific, previously-audited version of the code remaining stable between transaction construction and execution.

### Proof of Concept
1. Bob deploys `DeployGlobalContractAction { code: honest_wasm, deploy_mode: AccountId }` from `bob.near`.
2. Alice's account (`alice.near`) executes `UseGlobalContractAction { contract_identifier: AccountId(bob.near) }`, adopting `honest_wasm` as its code, per `use_global_contract` [3](#0-2) .
3. Alice signs and broadcasts `FunctionCall("withdraw", ...)` against `alice.near`, expecting `honest_wasm`'s withdraw logic to run.
4. Bob observes Alice's transaction in the mempool/tx pool and submits `DeployGlobalContractAction { code: malicious_wasm, deploy_mode: AccountId }` from `bob.near` with higher priority/earlier inclusion in the same or an earlier block.
5. When Alice's `FunctionCall` receipt is applied, `RuntimeContractIdentifier::resolve` fetches the now-updated code hash for `GlobalContractIdentifier::AccountId(bob.near)` from current trie state [4](#0-3)  and the runtime executes `malicious_wasm`'s `withdraw` implementation instead of the one Alice inspected and expected, e.g. redirecting funds to `bob.near`.
6. Alice's originally-expected outcome never occurs; value moves according to Bob's newly deployed logic, with no on-chain mechanism for Alice to have prevented or detected the swap before her transaction executed.

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

**File:** docs/RuntimeSpec/Actions.md (L440-449)
```markdown
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

**File:** runtime/runtime/src/global_contracts.rs (L76-108)
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
    Ok(())
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

**File:** runtime/runtime/src/contract_code.rs (L52-67)
```rust
        if account_id.get_account_type() == AccountType::EthImplicitAccount {
            // Accounts that look like eth implicit accounts and have existed prior to the
            // eth-implicit accounts protocol change (these accounts are discussed in the
            // description of #11606) may have something else deployed to them. Only return
            // something here if the accounts have a wallet contract hash. Otherwise use the
            // regular path to grab the deployed contract.
            if LegacyEthWallet::resolve(local_hash).is_some() {
                // ETH implicit wallet accounts use global contracts, including
                // those created in old protocol versions.
                let global_hash = eth_wallet_global_contract_hash(chain_id);
                return Ok(RuntimeContractIdentifier::Global {
                    code_hash: global_hash,
                    identifier: GlobalContractIdentifier::CodeHash(global_hash),
                });
            }
        }
```

**File:** runtime/runtime/src/contract_code.rs (L91-106)
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
```

**File:** runtime/runtime/src/lib.rs (L684-700)
```rust
            Action::FunctionCall(function_call) => {
                metrics::ACTION_CALLED_COUNT.function_call.inc();
                let account = account.as_mut().expect(EXPECT_ACCOUNT_EXISTS);
                let account_contract = account.contract().into_owned();
                let contract_id = RuntimeContractIdentifier::resolve(
                    account_id,
                    account_contract,
                    &state_update,
                    &epoch_info_provider.chain_id(),
                    AccessOptions::DEFAULT,
                )?;
                let contract = preparation_pipeline.get_contract(
                    receipt,
                    contract_id.clone(),
                    action_index,
                    None,
                );
```
