## Analog Confirmed

### Title
Unprivileged accounts sharing a `GlobalContractIdentifier::CodeHash` can grief the shared distribution nonce and silently drop legitimate `DeployGlobalContract` code installs on a shard - (File: `runtime/runtime/src/global_contracts.rs`)

### Summary
The Lido bug's core pattern — an unprivileged, arbitrary caller can invoke a public function that increments a shared counter that is later used to gate/validate a separate critical operation, thereby blocking that operation for a legitimate party — has a structural analog in nearcore's global contract distribution mechanism. The `GlobalContractNonce` used to validate freshness of `GlobalContractDistributionReceipt` deliveries is keyed only by content hash (`GlobalContractCodeIdentifier::CodeHash`), not by the deploying account, so any account that deploys byte-identical contract code shares and can increment the same nonce counter that another, unrelated deployer relies on.

### Finding Description
When an account submits `Action::DeployGlobalContract` with `GlobalContractDeployMode::CodeHash`, `initiate_distribution()` computes `id = GlobalContractIdentifier::CodeHash(hash(&contract_code))` and immediately increments a nonce stored under `TrieKey::GlobalContractNonce { identifier }` via `increment_nonce()`: [1](#0-0) 

Critically, this identifier depends only on the content hash of the code, not on the deploying `account_id`. Any account can trigger the exact same identifier/nonce slot simply by deploying byte-identical code: [2](#0-1) 

Each shard maintains its own local copy of this nonce (it's a `TrieUpdate` write, applied per-shard as the distribution receipt is processed at each hop), and `apply_distribution_current_shard()` uses `check_and_update_nonce()` to gate whether the code is actually written to that shard's state: [3](#0-2) 

If `incoming_nonce < stored_nonce`, the receipt is treated as stale and the function returns early — the contract code is never written to that shard, with no error reported anywhere: [4](#0-3) 

Because the nonce slot is shared by content hash across all accounts (not scoped per-deployer), an attacker who deploys the same byte-identical code (e.g., a copy of a known/public contract) from an account whose transactions land on a target shard can independently pump that shard's local `GlobalContractNonce` ahead of the value carried by a legitimate deployer's in-flight `GlobalContractDistributionReceipt`. When the legitimate receipt then arrives at that shard (receipts are forwarded shard-by-shard sequentially via `forward_distribution_next_shard`), its nonce is now stale relative to the attacker-inflated value, so the code silently fails to install on that shard while the deploying transaction itself reports success. This mirrors the Lido pattern exactly: an unprivileged, unrelated caller increments a shared index that gates a different party's critical operation, causing it to be silently blocked.

### Impact Explanation
A successfully-executed `DeployGlobalContract` action can have its actual code-installation effect permanently and silently dropped on one or more shards due to griefing by any other account, with no error surfaced to the deployer. This leaves the global contract present on some shards but missing on others (a stored, undeliverable "lost" receipt effect), and any account whose accounts live on the affected shard(s) that later calls `Action::UseGlobalContract` for that code hash will permanently fail with `GlobalContractDoesNotExist`, since `use_global_contract()` checks for local shard presence of the `GlobalContractCode` key: [5](#0-4) 
This constitutes a receipt-loss condition — the intended effect of an accepted, gas-paid action is silently discarded — and permanently blocks the affected shard(s) from ever using that contract code unless a fresh higher-nonce redeploy happens to outrun further griefing.

### Likelihood Explanation
Exploitation requires no special privilege: any account can submit a `DeployGlobalContract` transaction with `GlobalContractDeployMode::CodeHash` using byte-identical code to a target's deployment (trivial if the code is public, e.g. a well-known open-source contract), and can repeat this from an account whose transactions are processed on the target shard(s) before the victim's distribution receipt arrives there. The check-and-update logic in `check_and_update_nonce` explicitly allows equal nonces but silently rejects any receipt whose nonce is behind the current stored value, so a modest number of griefing deploys is sufficient to outrun a legitimate one-shot deployment.

### Recommendation
Scope the `GlobalContractNonce` (and the underlying distribution identifier used for freshness checks) by the deploying `account_id` in addition to the content hash for `CodeHash` deploy mode, so that unrelated accounts deploying identical bytes cannot share or grief the same nonce counter. Alternatively, surface an explicit error/refusal when a distribution receipt is dropped due to a stale nonce rather than silently discarding it, and/or bind the nonce check to the specific deploying account/receipt lineage rather than a purely content-derived key.

### Proof of Concept
1. Alice submits `Action::DeployGlobalContract{ code: C, deploy_mode: CodeHash }` from her account on shard `S_a`. This computes `id = CodeHash(hash(C))`, increments the nonce for `id` to `1` on shard `S_a`, and emits a `GlobalContractDistributionReceipt(id, nonce=1, code=C)` that will be forwarded sequentially to all other shards (`runtime/runtime/src/global_contracts.rs:151-171`, `288-333`).
2. Before that receipt reaches target shard `S_t`, Mallory — an unrelated account whose transactions execute on shard `S_t` — submits her own `Action::DeployGlobalContract{ code: C, deploy_mode: CodeHash }` (same bytes `C`, hence same `id`). This runs `initiate_distribution` locally on `S_t`, calling `increment_nonce` for the same `id`, bumping `S_t`'s local stored nonce to `1` (or higher if repeated) (`global_contracts.rs:143-189`).
3. When Alice's original distribution receipt (`nonce=1`) finally arrives at shard `S_t`, `check_and_update_nonce` compares `incoming_nonce(1)` against `stored_nonce` (now `>=1` due to Mallory, or higher if Mallory repeated the deploy). If Mallory's nonce is strictly ahead, `incoming_nonce < stored_nonce` is true, `check_and_update_nonce` returns `false`, and `apply_distribution_current_shard` returns early without writing `TrieKey::GlobalContractCode` for `id` on shard `S_t` (`global_contracts.rs:191-269`).
4. Alice's original `DeployGlobalContract` transaction still reports success (no error), but the code `C` under identifier `id` never gets installed on shard `S_t`. Any account on `S_t` calling `Action::UseGlobalContract{ contract_identifier: id }` will permanently fail with `GlobalContractDoesNotExist`, even though the deployment "succeeded" from Alice's perspective.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L76-94)
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
```

**File:** runtime/runtime/src/global_contracts.rs (L151-171)
```rust
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

**File:** runtime/runtime/src/global_contracts.rs (L173-189)
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
```

**File:** runtime/runtime/src/global_contracts.rs (L191-207)
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
```

**File:** runtime/runtime/src/global_contracts.rs (L248-269)
```rust
// Checks if the incoming nonce is fresh and updates the stored nonce. Returns
// true if the nonce is fresh, false if it's stale. The nonce is set
// immediately and the freshness check allows the same nonce (>=).
fn check_and_update_nonce(
    global_contract_data: &GlobalContractDistributionReceipt,
    identifier: &GlobalContractCodeIdentifier,
    state_update: &mut TrieUpdate,
) -> Result<bool, RuntimeError> {
    let nonce_key = TrieKey::GlobalContractNonce { identifier: identifier.clone() };
    let stored_nonce = get_nonce(state_update, &nonce_key)?;
    let incoming_nonce = global_contract_data.nonce();

    // Allow the same nonce since the nonce is updated immediately when
    // initiating distribution to prevent multiple distributions with the same
    // nonce from being initiated.
    if incoming_nonce < stored_nonce {
        return Ok(false);
    }

    set_nonce(state_update, nonce_key, incoming_nonce);
    Ok(true)
}
```
