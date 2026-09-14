### Title
Any account can invalidate an in-flight `GlobalContractDistribution` receipt by redeploying identical code, causing silent loss of contract deployment on unreached shards - ([File: runtime/runtime/src/global_contracts.rs])

### Summary
`GlobalContractDeployMode::CodeHash` identifies a global contract purely by the hash of its bytecode, not by the deploying account. The freshness of a `GlobalContractDistributionReceipt` that is hopping shard-by-shard is gated by a nonce keyed only on that `GlobalContractIdentifier`. Because any account can submit a `DeployGlobalContract` action with the same bytecode, an unrelated, unprivileged caller can bump this shared nonce while a legitimate deployer's distribution receipt is still crossing shards, causing the runtime to silently drop the write of the contract code on shards it has not yet reached.

### Finding Description
When a contract is deployed with `GlobalContractDeployMode::CodeHash`, the identifier used for both the nonce and the trie key is `GlobalContractIdentifier::CodeHash(hash(&contract_code))` — i.e., derived only from the bytecode, with no binding to the depositing account: [1](#0-0) 

`initiate_distribution` increments a nonce stored at `TrieKey::GlobalContractNonce { identifier }` and writes it immediately, then creates a `GlobalContractDistributionReceipt` carrying that nonce, which is forwarded shard-by-shard (`forward_distribution_next_shard`) so that eventually every shard receives the code: [2](#0-1) 

When the distribution receipt is applied on each shard, `apply_distribution_current_shard` first checks nonce freshness via `check_and_update_nonce`; if the nonce is stale (i.e., a *newer* nonce has since been written for the same identifier), the code write is skipped entirely and zero compute is charged, silently dropping the distribution on that shard: [3](#0-2) 

Since the `CodeHash` identifier depends only on the bytes of the contract, not on the account submitting the `DeployGlobalContract` action, any account — not just the original deployer — can submit a transaction that deploys identical bytecode. Doing so calls `initiate_distribution` again for the same `GlobalContractIdentifier::CodeHash`, incrementing the shared nonce in `TrieKey::GlobalContractNonce`. If the original deployer's distribution receipt has not yet reached every shard (distribution is sequential, hopping one shard per receipt-forward as seen in `forward_distribution_next_shard`), the attacker's redeploy transaction can race ahead: on shards the original receipt has not yet reached, its (now-stale) nonce fails the freshness check and its code write is skipped — permanently, since the receipt is consumed and not retried. This mirrors the RocketPool report's pattern: a permissionless, timer/counter-driven multi-step operation intended to eventually complete for a legitimate actor can be invalidated by any third party racing an unprivileged, low-cost transaction that resets a shared, unauthenticated piece of state (there: a distribution timer; here: a distribution nonce).

### Impact Explanation
The original deployer pays the deploy-global-contract gas fee expecting the code to become available on all shards, but a malicious or merely coincidental redeploy by an unrelated account of byte-identical code can cause the code to never be written on shards not yet reached by the original distribution — a state divergence between shards for a supposedly globally-available contract, and permanent, silent partial failure of a paid-for action (the code write is skipped with zero compute cost and no error/refund to the original submitter). This is a state-transition/availability inconsistency: some shards have the contract code committed under the identifier, others never do, even though the protocol's design intends full distribution. Contracts referencing this global contract by `CodeHash` from accounts on the un-reached shards will then fail to execute (`UseGlobalContract` invocations), effectively freezing functionality tied to that deployment on those shards with no recovery path exposed to the original deployer.

### Likelihood Explanation
The attack requires only that the attacker know (or guess) the exact bytecode being deployed and submit their own `DeployGlobalContract(CodeHash)` transaction while the victim's distribution receipt is still in flight across shards — bytecode for common/well-known contracts (e.g., popular NEP standards, the wallet contract, widely reused templates) is public, and the forwarding is sequential (one shard hop per applied receipt), giving a multi-block window. This makes the race feasible for a moderately resourced attacker monitoring `DeployGlobalContract` actions in the mempool/chunks, though it is a griefing/timing-race rather than a trivial single-transaction exploit.

### Recommendation
Bind the nonce/freshness check (and/or the distribution receipt's identity) to the specific deployment transaction/receipt rather than only to the shared `GlobalContractIdentifier`, e.g., only allow a *newer* nonce to supersede an *older* one if it originates from the same logical deployment lineage, or make `check_and_update_nonce` monotonic per (identifier, target_shard, already_delivered_shards) tuple so that a later, independent deploy cannot retroactively invalidate an already-in-flight distribution's remaining shard hops. Alternatively, allow concurrent distributions for the same `CodeHash` identifier to both complete (since the underlying code is identical) rather than short-circuiting the older one to zero compute/no-op.

### Proof of Concept
1. Account `A` submits `DeployGlobalContract` with bytecode `B` (`GlobalContractDeployMode::CodeHash`). This triggers `initiate_distribution`, incrementing `GlobalContractNonce` for `CodeHash(hash(B))` to `n`, and emits a `GlobalContractDistributionReceipt(nonce=n)` targeting shard 0, to be forwarded sequentially to shards 1..N (`forward_distribution_next_shard`), as exercised by the multi-shard forwarding tests: [4](#0-3) 
2. Before the distribution receipt reaches, say, shard 2, account `M` (unrelated to `A`) submits its own `DeployGlobalContract` with the identical bytecode `B`. This calls `initiate_distribution` again for the same identifier, bumping the nonce to `n+1` and emitting `M`'s own distribution receipt starting again from shard 0.
3. When `A`'s original receipt (nonce `n`) is applied on shard 2, `check_and_update_nonce` sees the stored nonce is now `n+1` (newer), determines `A`'s receipt nonce is stale, and `apply_distribution_current_shard` returns early without writing the code, per: [5](#0-4) 
4. Depending on timing, `M`'s receipt may also stall/interleave, and shards can end up with an inconsistent view of whether the global contract exists — `A` paid the deploy fee, but the code is never durably written on shard 2 via `A`'s deployment lineage. Existing repo tests such as `test_deploy_global_contract_compute_cost_splits_chunks` ( [6](#0-5) ) already confirm that nonce staleness makes a distribution receipt "short-circuit to zero compute" — the same mechanism that an unrelated third party can deliberately trigger against a victim's in-flight distribution.

Note: due to index size limits, I was not able to load the full body of `runtime/runtime/src/actions.rs` (where the `DeployGlobalContract` action's authorization/validation logic lives) to fully confirm there is no additional binding check (e.g., requiring the deploying account to match some registry) that would block this race for `CodeHash` mode. This should be verified directly by a Devin session with full file access before treating this as fully confirmed.

### Citations

**File:** runtime/runtime/src/global_contracts.rs (L151-158)
```rust
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
```

**File:** runtime/runtime/src/global_contracts.rs (L159-171)
```rust
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

**File:** test-loop-tests/src/tests/global_contracts_distribution.rs (L405-436)
```rust
/// Tests that GlobalContractDistribution receipts have ReceiptToTx entries, including
/// forwarded distribution receipts that hop across shards.
#[test]
// Spice + resharding triggers a refcount bug in GC (testdb "Inserting value
// with non-positive refcount"). Re-enable once that is resolved.
#[cfg_attr(feature = "protocol_feature_spice", ignore)]
fn test_global_distribution_receipt_has_receipt_to_tx() {
    init_test_logger();
    let mut env = GlobalContractsReshardingTestEnv::setup();
    let expected_new_shard_layout_height = EPOCH_LENGTH * 2 + 2;
    let send_deploy_tx_height = expected_new_shard_layout_height - 3;

    env.run_until_head_height(send_deploy_tx_height);

    // Deploy global contract.
    let deploy_user = env.users[0].clone();
    let code = ContractCode::new(near_test_contracts::rs_contract().to_vec(), None);
    let node = env.chunk_producer_node();
    let tx = node.tx_deploy_global_contract(
        &deploy_user,
        code.code().to_vec(),
        GlobalContractDeployMode::CodeHash,
    );
    let deploy_tx = node.submit_tx(tx);

    env.run_until_head_height(expected_new_shard_layout_height);
    check_txs(&mut env.env.test_loop.data, &env.env.node_datas, &env.chunk_producer, &[deploy_tx]);

    // Run extra blocks so forwarded distribution receipts (which hop shard-by-shard)
    // have time to appear in chunks.
    let extra_blocks = 10;
    env.run_until_head_height(expected_new_shard_layout_height + extra_blocks);
```

**File:** test-loop-tests/src/tests/deploy_compute_cost.rs (L115-139)
```rust
const GLOBAL_CONTRACT_SIZE: usize = 1000;

/// Two global-contract deploys must execute in different chunks: each
/// `GlobalContractDistribution` receipt charges
/// `deploy_global_contract_execution_base + per_byte * code_len` of compute,
/// and we pin the chunk's compute budget (chunk `gas_limit`, which today doubles
/// as `compute_limit`) to exactly that value. Each receipt then saturates the
/// chunk on its own, deferring the next one to the delayed-receipts queue.
///
/// The two contracts have slightly different sizes (and thus different code
/// hashes / identifiers) so both distribution receipts do full work; with a
/// shared identifier the second deploy would bump the on-chain nonce before
/// the first distribution receipt is applied, making it stale and short-circuit
/// to zero compute. We also use two distinct signer accounts because SPICE's
/// pending-tx queue enforces deploy exclusivity per signer (NEP-611), which
/// would otherwise serialize the two deploys into separate source chunks.
#[test]
fn test_deploy_global_contract_compute_cost_splits_chunks() {
    init_test_logger();

    let runtime_config_store = RuntimeConfigStore::new(None);
    let fees = &runtime_config_store.get_config(PROTOCOL_VERSION).fees;
    let compute_per_receipt = fees.deploy_global_contract_execution_base
        + (GLOBAL_CONTRACT_SIZE as u64) * fees.deploy_global_contract_execution_per_byte;
    let gas_limit = Gas::from_gas(compute_per_receipt);
```
