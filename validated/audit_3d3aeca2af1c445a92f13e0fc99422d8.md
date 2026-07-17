### Title
Global contract deployer can silently replace `AccountId`-mode WASM to drain all opted-in accounts - (File: `runtime/runtime/src/global_contracts.rs`)

### Summary

`GlobalContractDeployMode::AccountId` allows any unprivileged account to deploy a global contract that is fully mutable by the deployer at any time. Accounts that opt into such a contract via `UseGlobalContract` with `GlobalContractIdentifier::AccountId` permanently bind their execution to the deployer's latest WASM — there is no version pinning, no re-opt-in requirement, and no notification mechanism. A malicious deployer can attract users with a legitimate contract, then silently redeploy a fund-draining WASM, causing every opted-in account to execute attacker-controlled code on its next function call.

### Finding Description

**Root cause — `action_deploy_global_contract`** (`runtime/runtime/src/global_contracts.rs:23-61`):

`action_deploy_global_contract` places no restriction on who may overwrite an existing `AccountId`-keyed global contract or what the replacement code may do. The only check is that the deployer has sufficient balance to cover storage cost. The new WASM is written unconditionally to `TrieKey::GlobalContractCode { identifier: GlobalContractCodeIdentifier::AccountId(deployer_id) }`, overwriting whatever was there before.

```
// runtime/runtime/src/global_contracts.rs:51-58
initiate_distribution(
    state_update,
    account_id.clone(),
    deploy_contract.code.clone(),   // ← attacker-controlled bytes, no validation
    &deploy_contract.deploy_mode,
    apply_state.shard_id,
    result,
)?;
```

**Propagation — `apply_distribution_current_shard`** (`runtime/runtime/src/global_contracts.rs:189-233`):

The distribution receipt unconditionally overwrites the trie entry on every shard as long as the nonce is ≥ the stored nonce. After the malicious redeployment completes, every shard holds the attacker's WASM under the deployer's `AccountId` key.

**Execution — `RuntimeContractIdentifier::resolve`** (`runtime/runtime/src/contract_code.rs:43-46`):

When a function call is dispatched to any account whose `AccountContract` is `GlobalByAccount(deployer_id)`, `resolve` looks up the current trie value at `GlobalContractCodeIdentifier::AccountId(deployer_id)` — always the latest version, with no version pinning:

```rust
// runtime/runtime/src/contract_code.rs:43-46
let local_hash = match GlobalContractIdentifier::try_from(account_contract) {
    Ok(gci) => {
        let code_hash = gci.clone().hash(state_update, access)?;
        return Ok(RuntimeContractIdentifier::Global { code_hash, identifier: gci });
    }
```

**Attack path:**

1. Attacker deploys a legitimate global contract with `GlobalContractDeployMode::AccountId`.
2. Victim accounts call `UseGlobalContract { contract_identifier: GlobalContractIdentifier::AccountId(attacker_account) }`. Their account state is set to `AccountContract::GlobalByAccount(attacker_account)` — a live pointer to the deployer's latest WASM.
3. Attacker redeploys a malicious WASM (e.g., one that calls `promise_batch_action_transfer` to drain the account's balance to the attacker) using `DeployGlobalContractAction` with `GlobalContractDeployMode::AccountId`.
4. The malicious WASM propagates to all shards via `GlobalContractDistributionReceipt`.
5. Attacker (or anyone) sends a `FunctionCall` transaction to any victim account.
6. `RuntimeContractIdentifier::resolve` fetches the malicious WASM from the trie; the WASM executes in the context of the victim account and issues a cross-contract transfer to drain the victim's balance.

The `use_global_contract` function only checks that the global contract key exists at opt-in time — it does not re-validate on each call:

```rust
// runtime/runtime/src/global_contracts.rs:81-88
let key = TrieKey::GlobalContractCode { identifier: contract_identifier.clone().into() };
if !state_update.contains_key(&key, AccessOptions::DEFAULT)? {
    result.result = Err(ActionErrorKind::GlobalContractDoesNotExist { ... }.into());
    return Ok(());
}
// ← no further validation; account is bound to deployer's latest WASM
account.set_contract(contract);
```

### Impact Explanation

Every account that has opted into the global contract via `AccountId` mode will execute the attacker's replacement WASM on its next function call. The malicious WASM runs with the full authority of the victim account and can issue cross-contract calls to transfer the victim's entire NEAR balance to the attacker. This constitutes direct, unauthorized movement of assets from an unbounded number of victim accounts. The deployer can time the malicious redeployment to maximize the number of affected accounts and the total value at risk.

### Likelihood Explanation

Deploying a global contract requires no privileged access — any NEAR account with sufficient balance can do it. The `AccountId` mode is explicitly documented as allowing the deployer to update the contract for all users, so the mechanism is reachable through normal, supported protocol flows. An attacker can build a credible ecosystem (e.g., a shared utility library or a staking helper) to attract opt-ins before executing the replacement. The deployer can change the contract at any time without any on-chain notification to opted-in accounts.

### Recommendation

1. **Version-pin opt-ins**: Store the nonce at the time of `UseGlobalContract` in the account's `AccountContract::GlobalByAccount` variant. On function call dispatch, reject execution if the stored nonce does not match the current global contract nonce, forcing the account owner to explicitly re-opt-in after each update.
2. **Emit an on-chain event on update**: Record a state change or emit a receipt that opted-in accounts can observe, giving them a window to switch contracts before the new WASM is executed.
3. **Separate mutable and immutable modes clearly in UX/tooling**: Ensure that wallets and dApps warn users when they are opting into a mutable (`AccountId`-mode) global contract, as opposed to an immutable (`CodeHash`-mode) one.

### Proof of Concept

```
// Step 1: attacker.near deploys a legitimate global contract
Action::DeployGlobalContract(DeployGlobalContractAction {
    code: legitimate_wasm.into(),
    deploy_mode: GlobalContractDeployMode::AccountId,
})
// → TrieKey::GlobalContractCode { AccountId("attacker.near") } = legitimate_wasm

// Step 2: victim.near opts in
Action::UseGlobalContract(UseGlobalContractAction {
    contract_identifier: GlobalContractIdentifier::AccountId("attacker.near".parse().unwrap()),
})
// → victim.near account state: AccountContract::GlobalByAccount("attacker.near")

// Step 3: attacker.near redeploys malicious WASM
Action::DeployGlobalContract(DeployGlobalContractAction {
    code: malicious_drain_wasm.into(),   // calls promise_batch_action_transfer to attacker
    deploy_mode: GlobalContractDeployMode::AccountId,
})
// → TrieKey::GlobalContractCode { AccountId("attacker.near") } = malicious_drain_wasm
//   (distributed to all shards via GlobalContractDistributionReceipt)

// Step 4: anyone calls a function on victim.near
Action::FunctionCall(FunctionCallAction { method_name: "any_method".into(), ... })
// → RuntimeContractIdentifier::resolve fetches malicious_drain_wasm
// → malicious_drain_wasm executes in victim.near's context
// → promise_batch_action_transfer drains victim.near's balance to attacker.near
```

The divergent trie value is `TrieKey::GlobalContractCode { identifier: GlobalContractCodeIdentifier::AccountId("attacker.near") }`: before the attack it holds the legitimate WASM hash; after the redeployment it holds the malicious WASM hash. Every account bound to `AccountContract::GlobalByAccount("attacker.near")` silently transitions to executing the malicious code with no protocol-level gate.