This confirms the question's own premise: the code is self-contained metrics reporting with no consensus-relevant sink.

### Title
No Vulnerability — `PrioritizationFeeMetrics::accumulate_total_prioritization_fee` saturation is metrics-only and never touches capitalization or consensus state - ([File: runtime/src/prioritization_fee.rs])

### Summary
`total_prioritization_fee` is a `Saturating<u64>` field inside the private `PrioritizationFeeMetrics` struct, used only to populate `datapoint_info!("block_prioritization_fee", ...)` telemetry via `PrioritizationFeeMetrics::report`. It is never read back into fee distribution, `Bank::capitalization`, hashing, or snapshot logic, so saturating it has no effect beyond a reported number being capped at `u64::MAX`.

### Finding Description
`PrioritizationFee::update` calls `self.metrics.accumulate_total_prioritization_fee(prioritization_fee)` [1](#0-0)  which simply does `self.total_prioritization_fee += val` on a `Saturating<u64>` [2](#0-1) . This value is stored inside `PrioritizationFeeMetrics`, a private struct nested in `PrioritizationFee` [3](#0-2) , and the only consumer of the field is `PrioritizationFeeMetrics::report`, which emits it as a datapoint for observability [4](#0-3) . `test_total_prioritization_fee` explicitly verifies the saturation behavior, confirming it is intended, bounded behavior rather than a bug [5](#0-4) .

Actual fee collection and capitalization accounting is a completely separate code path: transaction fees (including priority fees) are tracked via `Bank::collector_fee_details` (`CollectorFeeDetails { transaction_fee, priority_fee }`) and distributed/burned through `Bank::distribute_transaction_fee_details`, which mutates `bank.capitalization()` based on actual collected lamports, not on the `PrioritizationFeeMetrics` accumulator [6](#0-5) . The `PrioritizationFeeCache`/`PrioritizationFee` structures exist solely to track minimum compute-unit-price stats for RPC/fee-estimation purposes (`get_min_compute_unit_price`, `get_writable_account_fee`) and metrics reporting, never feeding into consensus-critical state, hashing, or snapshotting.

### Impact Explanation
No impact. `total_prioritization_fee` saturating at `u64::MAX` only caps a metrics datapoint used for observability/dashboards; it does not affect `Bank::capitalization`, account balances, hashing, or any consensus-relevant computation. This falls under the explicitly out-of-scope "metrics" category per the audit rules.

### Likelihood Explanation
Not applicable — while an attacker could trivially cause saturation by submitting transactions with large `compute_unit_price`/priority fees, doing so has no security consequence beyond a reporting artifact.

### Recommendation
No fix required for security purposes. If precise unsaturated metrics are desired for very high-fee slots, the field could be widened (e.g., `u128`) or metrics could log an overflow flag, but this is a cosmetic/observability improvement, not a security fix.

### Proof of Concept
Not applicable (no vulnerability). The existing `test_total_prioritization_fee` test already demonstrates and validates the saturating behavior [5](#0-4) , and `test_distribute_transaction_fee_details_normal` demonstrates that capitalization changes are derived from `bank.collector_fee_details`, not from `PrioritizationFeeMetrics` [7](#0-6) .

### Citations

**File:** runtime/src/prioritization_fee.rs (L40-42)
```rust
    fn accumulate_total_prioritization_fee(&mut self, val: u64) {
        self.total_prioritization_fee += val;
    }
```

**File:** runtime/src/prioritization_fee.rs (L69-127)
```rust
    fn report(&self, slot: Slot) {
        let &PrioritizationFeeMetrics {
            total_writable_accounts_count,
            relevant_writable_accounts_count,
            prioritized_transactions_count: Saturating(prioritized_transactions_count),
            non_prioritized_transactions_count: Saturating(non_prioritized_transactions_count),
            attempted_update_on_finalized_fee_count:
                Saturating(attempted_update_on_finalized_fee_count),
            total_prioritization_fee: Saturating(total_prioritization_fee),
            min_compute_unit_price,
            max_compute_unit_price,
            total_update_elapsed_us: Saturating(total_update_elapsed_us),
        } = self;
        datapoint_info!(
            "block_prioritization_fee",
            ("slot", slot as i64, i64),
            (
                "total_writable_accounts_count",
                total_writable_accounts_count as i64,
                i64
            ),
            (
                "relevant_writable_accounts_count",
                relevant_writable_accounts_count as i64,
                i64
            ),
            (
                "prioritized_transactions_count",
                prioritized_transactions_count as i64,
                i64
            ),
            (
                "non_prioritized_transactions_count",
                non_prioritized_transactions_count as i64,
                i64
            ),
            (
                "attempted_update_on_finalized_fee_count",
                attempted_update_on_finalized_fee_count as i64,
                i64
            ),
            (
                "total_prioritization_fee",
                total_prioritization_fee as i64,
                i64
            ),
            (
                "min_compute_unit_price",
                min_compute_unit_price.unwrap_or(0) as i64,
                i64
            ),
            ("max_compute_unit_price", max_compute_unit_price as i64, i64),
            (
                "total_update_elapsed_us",
                total_update_elapsed_us as i64,
                i64
            ),
        );
    }
```

**File:** runtime/src/prioritization_fee.rs (L149-162)
```rust
pub struct PrioritizationFee {
    // The minimum prioritization fee of transactions that landed in this block.
    min_compute_unit_price: u64,

    // The minimum prioritization fee of each writable account in transactions in this block.
    min_writable_account_fees: HashMap<Pubkey, u64>,

    // Default to `false`, set to `true` when a block is completed, therefore the minimum fees recorded
    // are finalized, and can be made available for use (e.g., RPC query)
    is_finalized: bool,

    // slot prioritization fee metrics
    metrics: PrioritizationFeeMetrics,
}
```

**File:** runtime/src/prioritization_fee.rs (L198-200)
```rust
                self.metrics
                    .accumulate_total_prioritization_fee(prioritization_fee);
                self.metrics.update_compute_unit_price(compute_unit_price);
```

**File:** runtime/src/prioritization_fee.rs (L372-389)
```rust
    #[test]
    fn test_total_prioritization_fee() {
        let mut prioritization_fee = PrioritizationFee::default();
        prioritization_fee.update(0, 10, vec![]);
        assert_eq!(10, prioritization_fee.metrics.total_prioritization_fee.0);

        prioritization_fee.update(10, u64::MAX, vec![]);
        assert_eq!(
            u64::MAX,
            prioritization_fee.metrics.total_prioritization_fee.0
        );

        prioritization_fee.update(10, 100, vec![]);
        assert_eq!(
            u64::MAX,
            prioritization_fee.metrics.total_prioritization_fee.0
        );
    }
```

**File:** runtime/src/bank/fee_distribution.rs (L674-721)
```rust
    #[test]
    fn test_distribute_transaction_fee_details_normal() {
        let initial_balance = 1000;
        let genesis = create_genesis_config_with_leader(0, &pubkey::new_rand(), initial_balance);
        let mut bank = Bank::new_for_tests(&genesis.genesis_config);
        let transaction_fee = 100;
        let priority_fee = 200;
        bank.collector_fee_details = RwLock::new(CollectorFeeDetails {
            transaction_fee,
            priority_fee,
        });
        let expected_burn = transaction_fee * bank.burn_percent() / 100;
        let expected_rewards = transaction_fee - expected_burn + priority_fee;

        let collector_id = *bank.leader_id();

        let initial_capitalization = bank.capitalization();
        let initial_collector_balance = bank.get_balance(&collector_id);
        bank.distribute_transaction_fee_details();
        let new_collector_balance = bank.get_balance(&collector_id);

        assert_eq!(
            initial_collector_balance + expected_rewards,
            new_collector_balance
        );
        assert_eq!(
            initial_capitalization - expected_burn,
            bank.capitalization()
        );
        let locked_rewards = bank.rewards.read().unwrap();
        assert_eq!(
            locked_rewards.len(),
            1,
            "There should be one reward distributed"
        );

        let reward_info = &locked_rewards[0];
        assert_eq!(
            reward_info.1.lamports, expected_rewards as i64,
            "The reward amount should match the expected deposit"
        );
        assert_eq!(
            reward_info.1.reward_type,
            RewardType::Fee,
            "The reward type should be Fee"
        );
    }

```
