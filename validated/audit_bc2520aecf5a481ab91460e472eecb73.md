### Title
Bootstrap Config Flag Disables All Resource-Bounds Validation, Allowing Zero-Fee Transactions Into the Priority Queue and Execution Without Fee Enforcement - (`crates/apollo_gateway/src/stateless_transaction_validator.rs`, `crates/apollo_mempool/src/fee_transaction_queue.rs`)

---

### Summary

The `validate_resource_bounds` boolean flag, shared across the gateway's stateless validator, stateful validator, and the mempool's fee queue, is intended to be set to `false` during a system bootstrap phase. When disabled, **all** resource-bounds checks are bypassed — including the zero-bounds guard — allowing any user to submit transactions with all-zero gas bounds. Those transactions are admitted by the gateway, placed directly into the mempool's **priority queue**, and executed by the blockifier with `enforce_fee = false`, meaning no fees are charged and no balance is required.

---

### Finding Description

**Root cause — stateless validator:**

`StatelessTransactionValidator::validate_resource_bounds` returns `Ok(())` immediately when `self.config.validate_resource_bounds == false`, skipping every check including the `ZeroResourceBounds` guard:

```rust
fn validate_resource_bounds(&self, tx: &RpcTransaction) -> ... {
    if !self.config.validate_resource_bounds {
        return Ok(());   // ← all checks skipped, zero bounds pass
    }
    if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0) {
        return Err(StatelessTransactionValidatorError::ZeroResourceBounds { ... });
    }
    ...
}
``` [1](#0-0) 

**Root cause — mempool priority queue:**

`FeeTransactionQueue::insert` places a transaction into the **priority queue** (not the pending queue) whenever `validate_resource_bounds` is `false`, regardless of the transaction's `max_l2_gas_price`:

```rust
let to_pending_queue =
    validate_resource_bounds && tx_reference.max_l2_gas_price < self.gas_price_threshold;
// When validate_resource_bounds == false → to_pending_queue == false → priority queue
``` [2](#0-1) 

This is explicitly tested and confirmed: [3](#0-2) 

**Root cause — blockifier fee enforcement:**

When all three resource bounds (`l1_gas`, `l2_gas`, `l1_data_gas`) are zero, `enforce_fee` returns `false`, so the blockifier skips all fee pre-validation and post-execution fee charging:

```rust
let expected_enforce_fee = l1_gas_bound + l1_data_gas_bound + l2_gas_bound > 0;
``` [4](#0-3) 

**The shared config pointer:**

The single `validate_resource_bounds` value is wired simultaneously to the stateless validator, stateful validator, and mempool via a config pointer: [5](#0-4) 

**Stateful validator also skips its check:** [6](#0-5) 

---

### Impact Explanation

During the bootstrap window (`validate_resource_bounds = false`), any unprivileged user can submit a transaction with all-zero resource bounds (`l1_gas = l2_gas = l1_data_gas = 0`, `max_price_per_unit = 0`). The complete path:

1. **Gateway stateless validation** — `ZeroResourceBounds` guard is skipped; transaction admitted.
2. **Gateway stateful validation** — L2 gas price threshold check is skipped; blockifier pre-validation is also bypassed because `enforce_fee = false` for zero bounds.
3. **Mempool** — transaction inserted into the **priority queue** (highest sequencing priority), not the pending queue.
4. **Batcher/blockifier** — transaction executed with `enforce_fee = false`; no fee is charged and no balance is required.

Zero-fee transactions injected during bootstrap remain in the priority queue even after `validate_resource_bounds` is re-enabled, because the mempool does not re-evaluate existing queue entries when the flag changes. This matches the external bug's pattern: the "emergency resume" path (re-enabling normal operation) does not retroactively protect against transactions admitted under the unprotected mode.

**Matching impact:** *High — Mempool/gateway/RPC admission accepts invalid transactions before sequencing.*

---

### Likelihood Explanation

The bootstrap flag is documented as a production mechanism:

> "Indicates that validations related to resource bounds are applied. It should be set to false during a system bootstrap." [7](#0-6) 

Any network participant who observes the node accepting zero-fee transactions (e.g., by monitoring the RPC or mempool) can exploit the window. The window duration is operator-controlled but can span multiple blocks.

---

### Recommendation

The `validate_resource_bounds = false` flag should **not** suppress the absolute zero-bounds guard. Separate the "bootstrap relaxation" (skipping the gas-price-threshold check) from the "zero-bounds rejection" (which should always be enforced):

```rust
fn validate_resource_bounds(&self, tx: &RpcTransaction) -> ... {
    // Always reject zero bounds, even during bootstrap.
    let resource_bounds = *tx.resource_bounds();
    if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0) {
        return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
    }

    if !self.config.validate_resource_bounds {
        return Ok(()); // Only skip the price-threshold check during bootstrap.
    }
    // ... remaining checks
}
```

Similarly, `FeeTransactionQueue::insert` should always route zero-price transactions to the pending queue, regardless of the `validate_resource_bounds` flag.

---

### Proof of Concept

1. Operator sets `validate_resource_bounds = false` (bootstrap mode).
2. Attacker submits an `InvokeV3` transaction with:
   ```json
   "resource_bounds": {
     "l1_gas":      { "max_amount": "0x0", "max_price_per_unit": "0x0" },
     "l2_gas":      { "max_amount": "0x0", "max_price_per_unit": "0x0" },
     "l1_data_gas": { "max_amount": "0x0", "max_price_per_unit": "0x0" }
   }
   ```
3. `StatelessTransactionValidator::validate_resource_bounds` returns `Ok(())` immediately (line 60–62).
4. `StatefulTransactionValidator::validate_resource_bounds` skips the threshold check (line 228).
5. `FeeTransactionQueue::insert` places the transaction in the priority queue (line 50).
6. Batcher dequeues it; blockifier computes `enforce_fee = false` (all bounds zero); transaction executes with no fee charged and no balance required.
7. Operator re-enables `validate_resource_bounds = true`; the zero-fee transaction is already committed. [8](#0-7) [9](#0-8)

### Citations

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-68)
```rust
    fn validate_resource_bounds(
        &self,
        tx: &RpcTransaction,
    ) -> StatelessTransactionValidatorResult<()> {
        if !self.config.validate_resource_bounds {
            return Ok(());
        }

        let resource_bounds = *tx.resource_bounds();
        // The resource bounds should be positive even without the tip.
        if ValidResourceBounds::AllResources(resource_bounds).max_possible_fee(Tip::ZERO) == Fee(0)
        {
            return Err(StatelessTransactionValidatorError::ZeroResourceBounds { resource_bounds });
```

**File:** crates/apollo_mempool/src/fee_transaction_queue.rs (L44-51)
```rust

        let to_pending_queue =
            validate_resource_bounds && tx_reference.max_l2_gas_price < self.gas_price_threshold;
        let new_tx_successfully_inserted = if to_pending_queue {
            self.pending_queue.insert(tx_reference.into())
        } else {
            self.priority_queue.insert(tx_reference.into())
        };
```

**File:** crates/apollo_mempool/src/fee_mempool_test.rs (L488-511)
```rust
fn test_add_bootstrap_tx_depends_on_config(#[values(true, false)] allow_bootstrap: bool) {
    let mut builder = MempoolTestContentBuilder::new();
    builder.config.static_config.validate_resource_bounds = !allow_bootstrap;
    builder.gas_price_threshold = GasPrice(7);
    let mut mempool = builder.build_full_mempool();

    let zero_bounds_tx = add_tx_input!(
        tx_hash: 1,
        address: "0x0",
        tx_nonce: 0,
        account_nonce: 0,
        tip: 0,
        max_l2_gas_price: 0
    );
    add_tx(&mut mempool, &zero_bounds_tx);

    let txs = vec![TransactionReference::new(&zero_bounds_tx.tx)];
    let (expected_priority, expected_pending) =
        if allow_bootstrap { (txs, vec![]) } else { (vec![], txs) };
    let expected_mempool_content = MempoolTestContentBuilder::new()
        .with_pending_queue(expected_pending)
        .with_priority_queue(expected_priority)
        .build();
    expected_mempool_content.assert_eq(&mempool.content());
```

**File:** crates/blockifier/src/transaction/account_transactions_test.rs (L299-316)
```rust
#[rstest]
fn test_all_bounds_combinations_enforce_fee(
    #[values(0, 1)] l1_gas_bound: u64,
    #[values(0, 1)] l1_data_gas_bound: u64,
    #[values(0, 1)] l2_gas_bound: u64,
) {
    let expected_enforce_fee = l1_gas_bound + l1_data_gas_bound + l2_gas_bound > 0;
    let account_tx = invoke_tx_with_default_flags(invoke_tx_args! {
        version: TransactionVersion::THREE,
        resource_bounds: create_gas_amount_bounds_with_default_price(
            GasVector {
                l1_gas: l1_gas_bound.into(),
                l2_gas: l2_gas_bound.into(),
                l1_data_gas: l1_data_gas_bound.into(),
            },
        ),
    });
    assert_eq!(account_tx.create_tx_info().enforce_fee(), expected_enforce_fee);
```

**File:** crates/apollo_node_config/src/node_config.rs (L157-169)
```rust
        (
            ser_pointer_target_param(
                "validate_resource_bounds",
                &true,
                "Indicates that validations related to resource bounds are applied. \
                It should be set to false during a system bootstrap.",
            ),
            set_pointing_param_paths(&[
                "gateway_config.static_config.stateful_tx_validator_config.validate_resource_bounds",
                "gateway_config.static_config.stateless_tx_validator_config.validate_resource_bounds",
                "mempool_config.static_config.validate_resource_bounds",
            ]),
        ),
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L223-243)
```rust
    async fn validate_resource_bounds(
        &self,
        executable_tx: &ExecutableTransaction,
    ) -> StatefulTransactionValidatorResult<()> {
        // Skip this validation during the systems bootstrap phase.
        if self.config.validate_resource_bounds {
            // TODO(Arni): getnext_l2_gas_price from the block header.
            let previous_block_l2_gas_price = self
                .gateway_fixed_block_state_reader
                .get_block_info()
                .await?
                .gas_prices
                .strk_gas_prices
                .l2_gas_price;
            self.validate_tx_l2_gas_price_within_threshold(
                executable_tx.resource_bounds(),
                previous_block_l2_gas_price,
            )?;
        }
        Ok(())
    }
```
