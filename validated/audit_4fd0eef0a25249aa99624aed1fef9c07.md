The code path is fully traceable. Here is the analysis:

---

## Full Chain Trace

**Step 1 — Fallback construction** (`api_impl.rs:1572-1594`)

When `pending_data.block.parent_block_hash() != latest_header.block_hash`, `read_pending_data` constructs a `DeprecatedPendingBlock` with only `eth_l1_gas_price` and `strk_l1_gas_price` copied from the latest header. `DeprecatedPendingBlock` has no `l1_data_gas_price` or `l2_gas_price` fields at all. [1](#0-0) 

**Step 2 — Zero return from accessors** (`pending_data.rs:155-168`)

`l1_data_gas_price()` and `l2_gas_price()` on `PendingBlockOrDeprecated::Deprecated` both return `GasPricePerToken::default()` = `{price_in_wei: 0, price_in_fri: 0}`. The comments even say "In older versions, data gas price was 0." [2](#0-1) 

**Step 3 — Conversion to `ExecutionPendingData`** (`pending.rs:19-20`)

`client_pending_data_to_execution_pending_data` directly assigns `l1_data_gas_price` and `l2_gas_price` from the block accessors, so the `ExecutionPendingData` carries `{0, 0}` for both. [3](#0-2) 

**Step 4 — `estimate_fee` uses the fallback data** (`api_impl.rs:1009-1013`)

When `BlockId::Tag(Tag::Pending)`, `estimate_fee` calls `read_pending_data` then `client_pending_data_to_execution_pending_data` and passes the result as `maybe_pending_data` to `exec_estimate_fee`. [4](#0-3) 

**Step 5 — `create_block_context` substitutes `NonzeroGasPrice::MIN`** (`lib.rs:384-395`)

`NonzeroGasPrice::new(0)` returns `Err`, so `.unwrap_or(NonzeroGasPrice::MIN)` substitutes `GasPrice(1)` for both `l1_data_gas_price` and `l2_gas_price`. The TODO comment at line 379 acknowledges this is a known gap. [5](#0-4) 

**Step 6 — Fee estimation reads from block context** (`objects.rs:165-179`)

`tx_execution_output_to_fee_estimation` reads `l1_data_gas_price` and `l2_gas_price` directly from `block_context.block_info().gas_prices`, which now hold `GasPrice(1)` instead of the committed values. [6](#0-5) 

---

## Verdict

### Title
`read_pending_data` fallback constructs `DeprecatedPendingBlock` causing `estimate_fee` to return `NonzeroGasPrice::MIN` for `l1_data_gas_price` and `l2_gas_price` — (`crates/apollo_rpc/src/v0_8/api/api_impl.rs`)

### Summary
When the cached pending block's `parent_block_hash` does not match the latest committed block hash, `read_pending_data` falls back to a synthetic `DeprecatedPendingBlock`. This type has no `l1_data_gas_price` or `l2_gas_price` fields, so their accessors return `{0, 0}`. `create_block_context` silently replaces zero with `NonzeroGasPrice::MIN = GasPrice(1)`. Any `estimate_fee` or `simulate_transactions` call with `BlockId::Tag(Tag::Pending)` during this window returns `l1_data_gas_price = 1` and `l2_gas_price = 1` regardless of the actual committed values.

### Finding Description
The fallback path at `api_impl.rs:1573-1594` deliberately uses `PendingBlockOrDeprecated::Deprecated` as a sentinel for "no real pending block." However, `DeprecatedPendingBlock` structurally cannot carry `l1_data_gas_price` or `l2_gas_price`. The accessors `l1_data_gas_price()` and `l2_gas_price()` on the `Deprecated` variant return `GasPricePerToken::default()` by design (lines 157-158, 164-165 of `pending_data.rs`). The conversion function `client_pending_data_to_execution_pending_data` (`pending.rs:19-20`) propagates these zeros into `ExecutionPendingData`. `create_block_context` (`lib.rs:384-395`) then applies `.unwrap_or(NonzeroGasPrice::MIN)`, silently substituting `GasPrice(1)` for both fields. The fee estimation output (`objects.rs:168-169`) reads these substituted values and returns them to the caller as authoritative gas prices.

### Impact Explanation
Any caller of `starknet_estimateFee` or `starknet_simulateTransactions` with `block_id = "pending"` during the fallback window receives `l1_data_gas_price = 1 wei/fri` and `l2_gas_price = 1 fri` instead of the real values (which on mainnet are on the order of `10^9`). This causes fee estimates to be off by many orders of magnitude for transactions that consume data gas or L2 gas, matching the "RPC fee estimation returns authoritative-looking wrong value" impact category.

### Likelihood Explanation
The fallback fires whenever the in-memory `pending_data` is stale relative to the latest committed block. This is a normal operational condition: it occurs on every block transition until the pending data subscriber updates the shared `Arc<RwLock<PendingData>>`. On a node that syncs from a feeder gateway, this window is brief but real and repeatable. No special privileges are required — any unprivileged user can call `estimate_fee` with `BlockId::Tag(Tag::Pending)`.

### Recommendation
Replace the `DeprecatedPendingBlock` sentinel with a `PendingBlock` (the `Current` variant) that copies all gas price fields from the latest header, including `l1_data_gas_price` and `l2_gas_price`. The fallback at `api_impl.rs:1573-1594` should be:

```rust
Ok(PendingData {
    block: PendingBlockOrDeprecated::Current(PendingBlock {
        parent_block_hash: latest_header.block_hash,
        l1_gas_price: latest_header.block_header_without_hash.l1_gas_price,
        l1_data_gas_price: latest_header.block_header_without_hash.l1_data_gas_price,
        l2_gas_price: latest_header.block_header_without_hash.l2_gas_price,
        l1_da_mode: latest_header.block_header_without_hash.l1_da_mode,
        timestamp: latest_header.block_header_without_hash.timestamp,
        sequencer_address: latest_header.block_header_without_hash.sequencer,
        starknet_version: latest_header.block_header_without_hash.starknet_version.to_string(),
        ..Default::default()
    }),
    ...
})
```

### Proof of Concept
1. Commit a block with `l1_data_gas_price = {wei: 1_000_000_000, fri: 2_000_000_000}` and `l2_gas_price = {wei: 3_000_000_000, fri: 4_000_000_000}`.
2. Set the in-memory `pending_data.block.parent_block_hash` to any value other than the committed block's hash (simulating a stale pending block).
3. Call `estimate_fee` with `BlockId::Tag(Tag::Pending)`.
4. Assert that the returned `l1_data_gas_price` equals `GasPrice(1)` (= `NonzeroGasPrice::MIN`) and `l2_gas_price` equals `GasPrice(1)`, not the committed values. [7](#0-6) [2](#0-1) [8](#0-7) [9](#0-8) [6](#0-5)

### Citations

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1009-1016)
```rust
        let maybe_pending_data = if let BlockId::Tag(Tag::Pending) = block_id {
            Some(client_pending_data_to_execution_pending_data(
                read_pending_data(&self.pending_data, &storage_txn).await?,
                self.pending_classes.read().await.clone(),
            ))
        } else {
            None
        };
```

**File:** crates/apollo_rpc/src/v0_8/api/api_impl.rs (L1570-1594)
```rust
    if pending_data.block.parent_block_hash() == latest_header.block_hash {
        Ok((*pending_data).clone())
    } else {
        Ok(PendingData {
            block: PendingBlockOrDeprecated::Deprecated(DeprecatedPendingBlock {
                parent_block_hash: latest_header.block_hash,
                eth_l1_gas_price: latest_header.block_header_without_hash.l1_gas_price.price_in_wei,
                strk_l1_gas_price: latest_header
                    .block_header_without_hash
                    .l1_gas_price
                    .price_in_fri,
                timestamp: latest_header.block_header_without_hash.timestamp,
                sequencer_address: latest_header.block_header_without_hash.sequencer,
                starknet_version: latest_header
                    .block_header_without_hash
                    .starknet_version
                    .to_string(),
                ..Default::default()
            }),
            state_update: ClientPendingStateUpdate {
                old_root: latest_header.block_header_without_hash.state_root,
                state_diff: Default::default(),
            },
        })
    }
```

**File:** crates/apollo_starknet_client/src/reader/objects/pending_data.rs (L155-168)
```rust
    pub fn l1_data_gas_price(&self) -> GasPricePerToken {
        match self {
            // In older versions, data gas price was 0.
            PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
            PendingBlockOrDeprecated::Current(block) => block.l1_data_gas_price,
        }
    }
    pub fn l2_gas_price(&self) -> GasPricePerToken {
        match self {
            // In older versions, L2 gas price was 0.
            PendingBlockOrDeprecated::Deprecated(_) => GasPricePerToken::default(),
            PendingBlockOrDeprecated::Current(block) => block.l2_gas_price,
        }
    }
```

**File:** crates/apollo_rpc/src/pending.rs (L1-24)
```rust
use apollo_rpc_execution::objects::PendingData as ExecutionPendingData;
use apollo_starknet_client::reader::objects::pending_data::PendingData as ClientPendingData;
use papyrus_common::pending_classes::PendingClasses;

pub(crate) fn client_pending_data_to_execution_pending_data(
    client_pending_data: ClientPendingData,
    pending_classes: PendingClasses,
) -> ExecutionPendingData {
    ExecutionPendingData {
        storage_diffs: client_pending_data.state_update.state_diff.storage_diffs,
        deployed_contracts: client_pending_data.state_update.state_diff.deployed_contracts,
        declared_classes: client_pending_data.state_update.state_diff.declared_classes,
        old_declared_contracts: client_pending_data.state_update.state_diff.old_declared_contracts,
        nonces: client_pending_data.state_update.state_diff.nonces,
        replaced_classes: client_pending_data.state_update.state_diff.replaced_classes,
        classes: pending_classes,
        timestamp: client_pending_data.block.timestamp(),
        l1_gas_price: client_pending_data.block.l1_gas_price(),
        l1_data_gas_price: client_pending_data.block.l1_data_gas_price(),
        l2_gas_price: client_pending_data.block.l2_gas_price(),
        l1_da_mode: client_pending_data.block.l1_da_mode(),
        sequencer: client_pending_data.block.sequencer_address(),
    }
}
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L379-396)
```rust
        // TODO(yair): What to do about blocks pre 0.13.1 where the data gas price were 0?
        gas_prices: GasPrices {
            eth_gas_prices: GasPriceVector {
                l1_gas_price: NonzeroGasPrice::new(l1_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_wei)
                    .unwrap_or(NonzeroGasPrice::MIN),
            },
            strk_gas_prices: GasPriceVector {
                l1_gas_price: NonzeroGasPrice::new(l1_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l1_data_gas_price: NonzeroGasPrice::new(l1_data_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
                l2_gas_price: NonzeroGasPrice::new(l2_gas_price.price_in_fri)
                    .unwrap_or(NonzeroGasPrice::MIN),
            },
```

**File:** crates/apollo_rpc_execution/src/objects.rs (L165-179)
```rust
    let gas_prices = &block_context.block_info().gas_prices;
    let (l1_gas_price, l1_data_gas_price, l2_gas_price) = (
        gas_prices.l1_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l1_data_gas_price(&tx_execution_output.price_unit.into()).get(),
        gas_prices.l2_gas_price(&tx_execution_output.price_unit.into()).get(),
    );

    let gas_vector = tx_execution_output.execution_info.receipt.gas;

    Ok(FeeEstimation {
        gas_consumed: gas_vector.l1_gas.0.into(),
        l1_gas_price,
        data_gas_consumed: gas_vector.l1_data_gas.0.into(),
        l1_data_gas_price,
        l2_gas_price,
```
