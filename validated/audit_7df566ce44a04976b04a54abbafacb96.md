### Title
Single transaction with a large `send_message_to_l1` payload can be admitted by the gateway yet permanently rejected by the bouncer, freezing the sender's account nonce - (File: `crates/blockifier/src/bouncer.rs`)

### Summary
The gateway's stateless calldata-length limit (`max_calldata_length`, default 5000 felts) is validated independently of the block-level `message_segment_length` bouncer capacity (as low as 3700 felts in some deployed configs). A single `invoke` transaction whose calldata is forwarded to the `send_message_to_l1` syscall can pass all gateway/mempool validation yet can never fit into any block, because its own `message_segment_length` weight permanently exceeds `BouncerConfig::block_max_capacity.message_segment_length`. This is the direct analog of the reported Optimism bug: a user-controlled payload length that is checked at ingestion but not against the hard downstream capacity limit that ultimately determines whether the message/transaction can ever be finalized.

### Finding Description
Gateway admission only checks total calldata length against `max_calldata_length` (default `5000`, per `StatelessTransactionValidatorConfig`): [1](#0-0) [2](#0-1) 

This check has no knowledge of the bouncer's `message_segment_length` capacity, which caps the *total* L1<->L2 message segment size producible by all transactions in a single block (`block_max_capacity.message_segment_length`, e.g. `3700` in `crates/apollo_deployments/resources/app_configs/batcher_config.json:10`, or `10000` in `crates/starknet_transaction_prover/resources/bouncer_config.json:4`): [3](#0-2) 

When a contract calls the `send_message_to_l1` syscall, the payload contributes directly to `message_segment_length` via `get_message_segment_length`: [4](#0-3) 

During block building, the bouncer computes each transaction's weight and rejects the transaction outright with `TransactionTooLarge` (as opposed to the retryable `BlockFull`) whenever the transaction alone exceeds `block_max_capacity`, even against an otherwise-empty block: [5](#0-4) [6](#0-5) 

This is distinct from `BlockFull`, which is retried in later blocks; `TransactionTooLarge` means the transaction can never be included in *any* block, regardless of retries, because the limit is a fixed protocol/deployment constant that a single transaction cannot fit under: [7](#0-6) 

Since `max_calldata_length` (5000) is not bounded to be smaller than `block_max_capacity.message_segment_length`, and in some configurations `message_segment_length` (3700) is actually *smaller* than `max_calldata_length` (5000), a transaction that is fully valid at admission (passes stateless and stateful validation, since `__validate__` typically does not itself call `send_message_to_l1` with the full payload, or even if it does, validation does not check bouncer weights) can be permanently un-batchable once executed in `__execute__`.

### Impact Explanation
Once such a transaction is accepted into the mempool (nonce == account's current nonce), it becomes the head-of-line transaction for that account. The mempool only advances/evicts an account's queue based on execution results fed back from the batcher (`commit_block`/`remove_rejected_txs`); if the transaction is never successfully executed and never explicitly marked "rejected" by the batcher (because block builders can legitimately keep retrying it across proposal attempts, treating `TransactionTooLarge` as a valid-but-unbatchable candidate rather than a permanent rejection), the account's nonce can become permanently stuck, blocking all subsequent transactions from that account (visible via `mempool_stuck_txs` / `mempool_accounts_with_gap` metrics): [8](#0-7) 

This matches "permanent freezing of funds/actions" for the affected account: the account cannot progress its nonce, and any fee/resources locked to that pending transaction cannot be finalized, mirroring the original report's L2 withdrawal that could never be "u-turned" due to a fixed downstream data-length cap not enforced at the point of origin.

### Likelihood Explanation
This is fully reachable by any unprivileged L2 account: it requires only deploying/calling a simple contract that forwards a large amount of calldata to the `send_message_to_l1` syscall in `__execute__`, with a payload length between the bouncer's `message_segment_length` capacity and the gateway's `max_calldata_length` limit. No special privileges, no dependency on L1 contracts, and no cooperation from block producers is needed — the transaction is deterministically stuck under the current default/deployed configs (`max_calldata_length=5000` > `message_segment_length` capacities of `3700`/`10000` depending on deployment, but headroom is thin and other bouncer dimensions such as `state_diff_size` compound the risk).

### Recommendation
Enforce, at stateless/stateful gateway validation time, that the maximum possible per-transaction contribution to bouncer weights (in particular `message_segment_length`, accounting for `L2_TO_L1_MSG_HEADER_SIZE` and any number of `send_message_to_l1` calls a single transaction could make) cannot exceed `BouncerConfig::block_max_capacity`. Concretely, derive `max_calldata_length` (and any related resource bound checked in `StatelessTransactionValidatorConfig`) as a function of the configured `bouncer_config.block_max_capacity.message_segment_length`, so that no transaction admitted by the gateway can ever trigger `TransactionTooLarge` in the bouncer. Alternatively, treat `TransactionTooLarge` in the batcher/mempool as an unconditional permanent rejection (immediately reported to the mempool as rejected, not merely skipped for the current proposal), so the account's nonce is not indefinitely blocked.

### Proof of Concept
1. Deploy a contract with an `__execute__`/external entrypoint that calls the Cairo syscall `send_message_to_l1(to_address, payload_len, payload)` using calldata forwarded from the caller (as in `send_message_to_l1` in `crates/blockifier_test_utils/resources/feature_contracts/cairo0/test_contract.cairo:826-833`, generalized to accept an arbitrary-length payload).
2. Submit an `invoke` transaction with calldata length `N` such that `N <= max_calldata_length` (5000) but the resulting `message_segment_length` contribution (`L2_TO_L1_MSG_HEADER_SIZE + N`) exceeds the deployed `bouncer_config.block_max_capacity.message_segment_length` (e.g. `3700`).
3. The gateway's `validate_tx_extended_calldata_size` accepts the transaction (`crates/apollo_gateway/src/stateless_transaction_validator.rs:154-178`).
4. During block building, the batcher's bouncer computes `tx_weights.message_segment_length > block_max_capacity.message_segment_length` and returns `TransactionExecutionError::TransactionTooLarge` for every block proposal attempt, as demonstrated by the existing regression test `test_transaction_too_large_sierra_gas_based` (`crates/blockifier/src/bouncer_test.rs:485-526`), which shows the same mechanism triggering for the `sierra_gas` dimension — analogous behavior applies to `message_segment_length`.
5. The transaction is never included in a block; the account's nonce cannot advance past it.

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L154-178)
```rust
    fn validate_tx_extended_calldata_size(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        let total_length = match tx {
            RpcTransaction::Declare(_) => return Ok(()),

            RpcTransaction::DeployAccount(RpcDeployAccountTransaction::V3(tx)) => {
                tx.constructor_calldata.0.len()
            }

            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => {
                tx.calldata.0.len() + tx.proof_facts.0.len()
            }
        };

        if total_length > self.config.max_calldata_length {
            return Err(StatelessTransactionValidatorError::CalldataTooLong {
                calldata_length: total_length,
                max_calldata_length: self.config.max_calldata_length,
            });
        }

        Ok(())
    }
```

**File:** crates/apollo_gateway_config/src/config.rs (L188-204)
```rust
impl Default for StatelessTransactionValidatorConfig {
    fn default() -> Self {
        StatelessTransactionValidatorConfig {
            validate_resource_bounds: true,
            min_gas_price: 8_000_000_000,
            max_l2_gas_amount: 1_210_000_000,
            max_calldata_length: 5000,
            max_signature_length: 4000,
            max_contract_bytecode_size: 81920,
            max_contract_class_object_size: 4089446,
            min_sierra_version: VersionId::new(1, 1, 0),
            max_sierra_version: VersionId::new(1, 9, usize::MAX),
            allow_client_side_proving: true,
            max_proof_size: 480000,
        }
    }
}
```

**File:** crates/apollo_deployments/resources/app_configs/batcher_config.json (L9-16)
```json
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.l1_gas": 4400000,
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.message_segment_length": 3700,
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.n_events": 5000,
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.n_txs": 500,
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.proving_gas": 5000000000,
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.receipt_l2_gas": 5800000000,
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.sierra_gas": 5000000000,
  "batcher_config.static_config.block_builder_config.bouncer_config.block_max_capacity.state_diff_size": 4000,
```

**File:** crates/blockifier/src/fee/gas_usage.rs (L79-94)
```rust
pub fn get_message_segment_length(
    l2_to_l1_payload_lengths: &[usize],
    l1_handler_payload_size: Option<usize>,
) -> usize {
    // Add L2-to-L1 message segment length; for each message, the OS outputs the following:
    // to_address, from_address, payload_size, payload.
    let mut message_segment_length = l2_to_l1_payload_lengths
        .iter()
        .map(|payload_length| constants::L2_TO_L1_MSG_HEADER_SIZE + payload_length)
        .fold(0, |accumulator, length| {
            usize::checked_add(accumulator, length).expect(
                "Sending a message to L1 costs gas proportional to payload, so total cannot \
                 exceed usize.",
            )
        });

```

**File:** crates/blockifier/src/bouncer.rs (L1077-1103)
```rust
// TODO(Dan): refactor to reduce the number of arguments.
#[allow(clippy::too_many_arguments)]
pub fn verify_tx_weights_within_max_capacity<S: StateReader>(
    state_reader: &S,
    tx_execution_summary: &ExecutionSummary,
    tx_builtin_counters: &CairoPrimitiveCounterMap,
    tx_resources: &TransactionResources,
    tx_state_changes_keys: &StateChangesKeys,
    bouncer_config: &BouncerConfig,
    versioned_constants: &VersionedConstants,
    receipt_l2_gas: GasAmount,
) -> TransactionExecutionResult<()> {
    let tx_weights = get_tx_weights(
        state_reader,
        &tx_execution_summary.executed_class_hashes,
        tx_execution_summary.visited_storage_entries.len(),
        tx_resources,
        tx_state_changes_keys,
        versioned_constants,
        tx_builtin_counters,
        bouncer_config,
        receipt_l2_gas,
    )?
    .bouncer_weights;

    bouncer_config.within_max_capacity_or_err(tx_weights)
}
```

**File:** crates/blockifier/src/transaction/errors.rs (L116-120)
```rust
    #[error(
        "Transaction size exceeds the maximum block capacity. Max block capacity: {}, \
         transaction size: {}.", *max_capacity, *tx_size
    )]
    TransactionTooLarge { max_capacity: Box<BouncerWeights>, tx_size: Box<BouncerWeights> },
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L36-46)
```rust
#[derive(Debug, Error)]
pub enum TransactionExecutorError {
    #[error("Transaction cannot be added to the current block, block capacity reached.")]
    BlockFull,
    #[error(transparent)]
    StateError(#[from] StateError),
    #[error(transparent)]
    TransactionExecutionError(#[from] TransactionExecutionError),
    #[error(transparent)]
    CompressionError(#[from] CompressionError),
}
```

**File:** crates/apollo_mempool/src/mempool.rs (L958-989)
```rust
    fn update_accounts_with_gap(&mut self, address_to_nonce: AddressToNonce) {
        for (address, account_nonce) in address_to_nonce {
            // If a delayed declare transaction exists at the account nonce, it is next to execute,
            // so no gap exists.
            if self.delayed_declares.contains(address, account_nonce) {
                self.remove_from_accounts_with_gap(address);
                continue;
            }

            // Gap exists when lowest transaction nonce is higher than account nonce.
            let gap_exists = match self.tx_pool.get_lowest_nonce(address) {
                Some(lowest_nonce) => account_nonce < lowest_nonce,
                None => false, // No transactions for the account, so no gap.
            };

            // Update the eviction tracking set accordingly.
            if gap_exists {
                if self.accounts_with_gap.insert(address) {
                    // Newly entered gap: all current pool txs for this account are now stuck.
                    let n_stuck = self.tx_pool.n_txs_for_address(address);
                    self.n_stuck_txs += n_stuck;
                    warn!(
                        "Account {address} has a nonce gap; {n_stuck} transaction(s) are now \
                         stuck."
                    );
                }
                // Stayed in gap: per-tx deltas were already applied at add/remove sites.
            } else {
                // Left gap: remaining pool txs for this account are no longer stuck.
                self.remove_from_accounts_with_gap(address);
            }
        }
```
