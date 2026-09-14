### Title
Global Contract `AccountId` deploy mode lets a permissionless account owner silently rug every account that "uses" its contract - (File: `runtime/runtime/src/global_contracts.rs`)

### Summary
`GlobalContractDeployMode::AccountId` is the direct NEAR analog of an upgradeable proxy: any account can publish WASM code addressed by its own `AccountId`, other accounts opt in via `UseGlobalContractAction`, and the publisher can later redeploy new code under the *same* `AccountId` reference at any time. Because the reference stored on a consumer account (`AccountContract::GlobalByAccount(id)`) is a live pointer, not a pinned code hash, every future call into a consuming account transparently executes whatever code the publisher currently has deployed - exactly the "admin swaps the implementation and rugs users" pattern from the Controller.sol finding.

### Finding Description
`action_deploy_global_contract` lets any signer deploy code and, in `AccountId` mode, register it under `GlobalContractIdentifier::AccountId(account_id)` [1](#0-0) . `initiate_distribution` derives the identifier as `GlobalContractIdentifier::AccountId(account_id.clone())` for that mode, and each redeploy just bumps a nonce and re-broadcasts a distribution receipt that overwrites the previously stored code at the same `TrieKey::GlobalContractCode { identifier }` [2](#0-1) ; `apply_distribution_current_shard` writes the new bytes over the old ones as soon as the nonce is fresh, with no user re-approval step [3](#0-2) .

A separate, unprivileged account opts in via `UseGlobalContractAction`. `use_global_contract` sets that consumer's `AccountContract` to `GlobalByAccount(id)` — a symbolic reference to the publisher's account, not a fixed code hash — and this binding is durable (persisted on the consumer account until it's changed again) [4](#0-3) . The `AccountId` mode's own doc comment states the intent plainly: "This allows the owner to update the contract for all its users" [5](#0-4) .

Because subsequent calls into a consumer account run whatever code currently lives at that publisher-owned identifier, the publisher (a completely permissionless, unprivileged account — reachable purely by sending `DeployGlobalContractAction`/`UseGlobalContractAction` transactions) plays the exact role of the "proxy admin" in the Controller.sol report: it can push new logic at will, and every account that previously opted in inherits the new logic automatically the next time it is invoked, without any fresh signature, approval, or transaction from the victim.

### Impact Explanation
Once code executes under a victim's account via `GlobalByAccount`, it runs with the victim account's own execution context, i.e. it can issue promises that transfer the victim account's NEAR balance or drive calls to NEP-141/other contracts on the victim's behalf (analogous to draining "allowances"/balances in the Solidity report). A malicious or later-compromised publisher account can:
1. Publish a benign, useful contract to attract adoption via `UseGlobalContractAction::AccountId`.
2. Later redeploy malicious code under the same `AccountId` (`DeployGlobalContractAction` with `deploy_mode: AccountId`), which silently propagates via the distribution-receipt mechanism to all shards.
3. Have that malicious code execute with the authority of every consumer account the next time each is invoked, moving funds (transfers, calls) out from under the account owner without any new consent from that owner.

This is a concrete unauthorized-value-movement primitive: unlike the "governance attack must happen first" precondition that got the original finding downgraded to Medium, here the "admin" is just an ordinary, permissionless account — no governance/validator compromise is required, only that some other account chose to reference the publisher's code by `AccountId`.

### Likelihood Explanation
Reaching this requires only two ordinary transactions from unprivileged accounts: the publisher's `DeployGlobalContractAction` (initial and later malicious redeploy) and the victim's own earlier `UseGlobalContractAction` referencing that publisher by `AccountId`. No validator, node-operator, or network-layer capability is needed; the whole primitive is reachable purely through the standard transaction/action-application path (`action_deploy_global_contract` / `action_use_global_contract` in `runtime/runtime/src/global_contracts.rs`) and the cross-shard `GlobalContractDistributionReceipt` propagation mechanism that pushes updates without further victim interaction [6](#0-5) . The main constraint is social: a victim account must have deliberately chosen to reference the publisher's `AccountId` rather than pinning to a specific `CodeHash`.

### Recommendation
Treat `GlobalContractDeployMode::AccountId` consumption as a trust relationship and mitigate at the protocol/tooling level: (1) strongly prefer/encourage `CodeHash` mode for any account holding funds, since that mode is documented as immutable ("This effectively makes the contract immutable") [7](#0-6) ; (2) consider requiring an explicit re-`UseGlobalContractAction` (or a timelock/notification) before a redeploy under `AccountId` mode takes effect for existing consumers, so that code changes cannot silently retroactively apply to accounts that already opted in; (3) surface in RPC/wallet UX that `AccountId`-mode contract usage is mutable by a third party and carries ongoing admin-key-equivalent risk, similar to how upgradeable-proxy risk is now flagged for users of such contracts off-chain.

### Proof of Concept
1. Publisher account `pub.near` deploys a benign global contract: `DeployGlobalContractAction { code: benign_wasm, deploy_mode: AccountId }` → distributes to `GlobalContractIdentifier::AccountId(pub.near)`.
2. Victim account `alice.near` sends `UseGlobalContractAction { contract_identifier: AccountId(pub.near) }`, setting `alice.near`'s `AccountContract` to `GlobalByAccount(pub.near)` [8](#0-7) .
3. Time passes; `alice.near` accumulates NEAR balance / token holdings and interacts normally with the contract it "trusts."
4. `pub.near` sends a new `DeployGlobalContractAction { code: malicious_wasm, deploy_mode: AccountId }`. `initiate_distribution` increments the nonce and the distribution receipt overwrites the code at the same `AccountId` identifier across shards [9](#0-8) .
5. The next `FunctionCall`/self-invocation on `alice.near` executes `malicious_wasm`, which issues `Promise::transfer` (or calls a token contract) moving `alice.near`'s balance to an attacker-controlled account — with no new signature or consent from Alice.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L25-63)
```rust
pub(crate) fn action_deploy_global_contract(
    state_update: &mut TrieUpdate,
    account: &mut Account,
    account_id: &AccountId,
    apply_state: &ApplyState,
    deploy_contract: &DeployGlobalContractAction,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let _span = tracing::debug_span!(target: "runtime", "action_deploy_global_contract").entered();

    let storage_cost = apply_state
        .config
        .fees
        .storage_usage_config
        .global_contract_storage_amount_per_byte
        .saturating_mul(deploy_contract.code.len() as u128);
    let Some(updated_balance) = account.amount().checked_sub(storage_cost) else {
        result.result = Err(ActionErrorKind::LackBalanceForState {
            account_id: account_id.clone(),
            amount: storage_cost,
        }
        .into());
        return Ok(());
    };
    result.tokens_burnt =
        result.tokens_burnt.checked_add(storage_cost).ok_or(IntegerOverflowError)?;
    account.set_amount(updated_balance);

    initiate_distribution(
        state_update,
        account_id.clone(),
        deploy_contract.code.clone(),
        &deploy_contract.deploy_mode,
        apply_state.shard_id,
        result,
    )?;

    Ok(())
}
```

**File:** runtime/runtime/src/global_contracts.rs (L76-109)
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
}
```

**File:** runtime/runtime/src/global_contracts.rs (L111-141)
```rust
pub(crate) fn apply_global_contract_distribution_receipt(
    receipt: &Receipt,
    apply_state: &ApplyState,
    epoch_info_provider: &dyn EpochInfoProvider,
    state_update: &mut TrieUpdate,
    receipt_sink: &mut ReceiptSink,
    receipt_to_tx: &mut Vec<(CryptoHash, ReceiptToTxInfo)>,
) -> Result<Compute, RuntimeError> {
    let _span = tracing::debug_span!(
        target: "runtime",
        "apply_global_contract_distribution_receipt",
    )
    .entered();

    let ReceiptEnum::GlobalContractDistribution(global_contract_data) = receipt.receipt() else {
        unreachable!("given receipt should be an global contract distribution receipt")
    };
    let compute =
        apply_distribution_current_shard(receipt, global_contract_data, apply_state, state_update)?;
    forward_distribution_next_shard(
        receipt,
        global_contract_data,
        apply_state,
        epoch_info_provider,
        state_update,
        receipt_sink,
        receipt_to_tx,
    )?;

    Ok(compute)
}
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

**File:** runtime/runtime/src/global_contracts.rs (L191-226)
```rust
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

```

**File:** core/primitives/src/action/mod.rs (L136-139)
```rust
    /// Contract is deployed under its code hash.
    /// Users will be able reference it by that hash.
    /// This effectively makes the contract immutable.
    CodeHash,
```

**File:** core/primitives/src/action/mod.rs (L140-144)
```rust
    /// Contract is deployed under the owner account id.
    /// Users will be able reference it by that account id.
    /// This allows the owner to update the contract for all its users.
    AccountId,
}
```
