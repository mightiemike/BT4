## Analysis: Reachable panic in gas/fee overflow checks (analog to CVE-2018-20217)

CVE-2018-20217 is a "reachable assertion" bug: an attacker can craft a request using an unusual-but-accepted field combination (old encryption type) that trips an internal `assert` in the KDC and crashes the daemon. Searching the sequencer for an equivalent pattern — validated input that still allows a value combination which triggers a Rust `panic!`/`.expect()` instead of returning a `Result` — turned up several such call sites in `blockifier`'s fee/gas and bouncer-weight arithmetic that are reachable directly from a submitted transaction's `resource_bounds` fields.

### Title
Reachable panic in gas-cost overflow checks via attacker-controlled `resource_bounds.max_price_per_unit` — (File: `crates/starknet_api/src/execution_resources.rs`)

### Summary
`GasVector::cost` computes `gas.checked_mul(price.get())` for each resource and `panic!`s on overflow instead of returning an error: [1](#0-0) 
The gateway's stateless validation only enforces a **lower** bound on `max_price_per_unit` (`min_gas_price`) and an **upper** bound on `l2_gas.max_amount`, but places no upper bound on `max_price_per_unit` itself: [2](#0-1) 
This mirrors the pattern flagged by the project's own coding rules ("Never panic on data reachable from requests"): [3](#0-2) 

The same "arithmetic overflow → `panic!`" pattern recurs throughout the fee/bouncer code that runs once a transaction is admitted and executed/charged:
- `GasVectorToL1GasForFee::to_l1_gas_for_fee` panics on L1/L2 gas conversion overflow: [4](#0-3) 
- `TransactionResources::to_gas_vector` panics on overflow combining starknet gas and computation gas: [5](#0-4) 
- `GasAmount::checked_add_panic_on_overflow` and its many callers in the bouncer (`compute_sierra_gas`, `compute_proving_gas`, `CasmHashComputationData::total_gas`, `Bouncer::try_update`/`update`) panic on overflow rather than erroring: [6](#0-5) [7](#0-6) [8](#0-7) 

The project's own test suite explicitly exercises and expects this panic behavior for the L1/L1-data/L2 gas cost overflow case, confirming the code path panics rather than returning an error under attacker-reachable inputs: [9](#0-8) 

### Finding Description
A transaction's `resource_bounds` (`max_amount`, `max_price_per_unit` per resource) are fully attacker-controlled fields on any `Invoke`/`Declare`/`DeployAccount` V3 transaction. `StatelessTransactionValidator::validate_resource_bounds` only checks:
1. that the resulting `max_possible_fee` is non-zero, and
2. that `l2_gas.max_price_per_unit` is **not below** `min_gas_price`, and
3. that `l2_gas.max_amount` does not **exceed** `max_l2_gas_amount` (for non-Declare txs).

There is no corresponding upper bound on `max_price_per_unit`, nor on `l1_gas`/`l1_data_gas` amounts/prices. Consequently a transaction can pass gateway admission with, e.g., a small nonzero `max_amount` and a `max_price_per_unit` near the top of its integer range, such that `amount * price` overflows the internal fixed-width integer type used by `GasVector::cost`/`GasAmount` arithmetic. Instead of surfacing this as a `Result::Err` (as the crate's own style rules mandate for request-derived data), the code calls `.unwrap_or_else(|| panic!(...))`, i.e., an unhandled panic.

This is structurally analogous to CVE-2018-20217: the request passes all *documented* validation, but an unusual (rarely exercised) field-value combination reaches an internal invariant check that was written as a hard assertion/panic rather than a recoverable error, crashing the process that processes it.

### Impact Explanation
If this fee/gas arithmetic panic fires in the batcher's block-building or execution path, or during the Starknet OS/blockifier re-execution used for state validation, it terminates the executing thread/process rather than rejecting only the offending transaction. Because every sequencer/full node must execute (or re-execute) the same block content to validate it, a transaction that reliably panics on this arithmetic would cause **every honest node reaching that code path to crash in the same way**, which can halt block production/validation network-wide — matching the "network unable to confirm new transactions" impact criterion.

### Likelihood Explanation
Likelihood depends on details I could not fully confirm within the available tool budget:
- Whether `GasPrice`/`Fee`/`GasAmount` widths (and any additional validation performed deeper in the pipeline, e.g. during fee charging or resource-bound-to-gas-vector conversion) leave enough numeric headroom for a single attacker-chosen `resource_bounds` value to actually trigger overflow, given `max_l2_gas_amount` is capped by gateway config (default ~1.21e9) while `max_price_per_unit`/`GasPrice` is a wider integer type.
- Whether the panic, if triggered, is caught by an existing `panic::catch_unwind` boundary (e.g., the concurrent-execution worker pool's `AbortIfPanic`/`catch_unwind` wrapper) and thus only fails a single transaction/chunk instead of crashing the whole process, or whether it propagates uncaught in the sequential/batcher execution path.

Because of this uncertainty, I present this as the strongest candidate analog found via static inspection, not a confirmed, end-to-end exploit chain. A background engineer would need to trace the exact call path from `StatefulTransactionValidator`/`BlockBuilder` through `AccountTransaction::execute` → fee charging → `GasVector::cost`/`checked_add_panic_on_overflow`, and construct a concrete `resource_bounds` value that survives all validation layers and still overflows, to fully confirm exploitability and blast radius (single-tx failure vs. process crash).

### Recommendation
- Replace all `.unwrap_or_else(|| panic!(...))` / `.expect(...)` calls on arithmetic derived from transaction fields (`GasVector::cost`, `GasVectorToL1GasForFee::to_l1_gas_for_fee`, `TransactionResources::to_gas_vector`, `GasAmount::checked_add_panic_on_overflow` and its bouncer callers, `vm_resource_to_gas_amount`, `cairo_primitives_to_gas`) with `Result`-returning equivalents that reject the transaction (e.g., `TransactionExecutionError`), consistent with the project's own "Never panic on data reachable from requests" rule.
- Add an explicit upper bound on `resource_bounds.*.max_price_per_unit` (and `l1_gas`/`l1_data_gas` amounts) in `StatelessTransactionValidator::validate_resource_bounds`, sized so that `amount * price` for every resource is provably representable without overflow given the widths of `GasAmount`/`Fee`/`GasPrice`.
- Add regression tests that submit transactions (through the gateway, not just unit-test the fee function directly) with maximal allowed `resource_bounds` values and assert graceful rejection rather than a panic anywhere in the execution/fee/bouncer pipeline.

### Proof of Concept
Conceptual (not independently executed end-to-end in this session):
1. Submit a V3 `Invoke`/`DeployAccount` transaction with `resource_bounds.l2_gas.max_amount` set to a small nonzero value and `max_price_per_unit` set near the top of its representable range (satisfying `min_gas_price` and `max_l2_gas_amount` checks, and yielding a nonzero `max_possible_fee`).
2. Gateway's `StatelessTransactionValidator::validate` accepts the transaction (no upper bound on price exists) — [2](#0-1) .
3. When the batcher/blockifier computes the transaction's gas cost/fee (`GasVector::cost`) or aggregates bouncer weights, the `amount * price` (or gas-amount addition) overflows the backing integer, hitting the `panic!` branch — [10](#0-9) , corroborated by the existing overflow-panic unit test — [9](#0-8) .
4. Depending on which execution path (sequential vs. concurrent worker pool) processes the transaction, this either crashes the executing thread/process outright or is caught by the worker pool's panic-propagation logic and re-raised as an unrecoverable error, still resulting in denial of service for block building/validation involving that transaction.

### Citations

**File:** crates/starknet_api/src/execution_resources.rs (L67-74)
```rust
    pub fn checked_add_panic_on_overflow(self, added_gas: GasAmount) -> GasAmount {
        self.checked_add(added_gas).unwrap_or_else(|| {
            panic!(
                "Addition overflow while adding gas. current gas: {self}, try to add
                 gas: {added_gas}.",
            )
        })
    }
```

**File:** crates/starknet_api/src/execution_resources.rs (L155-186)
```rust
    /// Computes the cost (in fee token units) of the gas vector (panicking on overflow).
    pub fn cost(&self, gas_prices: &GasPriceVector, tip: Tip) -> Fee {
        let tipped_l2_gas_price =
            gas_prices.l2_gas_price.checked_add(tip.into()).unwrap_or_else(|| {
                panic!(
                    "Tip overflowed: addition of L2 gas price ({}) and tip ({}) resulted in \
                     overflow.",
                    gas_prices.l2_gas_price, tip
                )
            });

        let mut sum = Fee(0);
        for (gas, price, resource) in [
            (self.l1_gas, gas_prices.l1_gas_price, Resource::L1Gas),
            (self.l1_data_gas, gas_prices.l1_data_gas_price, Resource::L1DataGas),
            (self.l2_gas, tipped_l2_gas_price, Resource::L2Gas),
        ] {
            let cost = gas.checked_mul(price.get()).unwrap_or_else(|| {
                panic!(
                    "{resource} cost overflowed: multiplication of gas amount ({gas}) by price \
                     per unit ({price}) resulted in overflow."
                )
            });
            sum = sum.checked_add(cost).unwrap_or_else(|| {
                panic!(
                    "Total cost overflowed: addition of current sum ({sum}) and cost of \
                     {resource} ({cost}) resulted in overflow."
                )
            });
        }
        sum
    }
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L56-88)
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
        }

        if resource_bounds.l2_gas.max_price_per_unit.0 < self.config.min_gas_price {
            return Err(StatelessTransactionValidatorError::MaxGasPriceTooLow {
                gas_price: resource_bounds.l2_gas.max_price_per_unit,
                min_gas_price: self.config.min_gas_price,
            });
        }

        // TODO(Arni): Consider adding a validation for max_l2_gas_amount for declare.
        if let RpcTransaction::Declare(_) = tx {
        } else if resource_bounds.l2_gas.max_amount.0 > self.config.max_l2_gas_amount {
            return Err(StatelessTransactionValidatorError::MaxGasAmountTooHigh {
                gas_amount: resource_bounds.l2_gas.max_amount,
                max_gas_amount: self.config.max_l2_gas_amount,
            });
        }

        Ok(())
    }
```

**File:** .claude/rules/code-style.md (L67-70)
```markdown
### Never panic on data reachable from requests
- Code reachable from HTTP handlers or external input must never use `.unwrap()`, `.expect()`, or unchecked indexing on values derived from that input
- Reserve panics for compile-time invariants where failure is a programmer bug, not a runtime possibility
- Return `Result` with a descriptive error variant, or use saturating/capping alternatives
```

**File:** crates/blockifier/src/fee/fee_utils.rs (L40-62)
```rust
impl GasVectorToL1GasForFee for GasVector {
    fn to_l1_gas_for_fee(
        &self,
        gas_prices: &GasPriceVector,
        versioned_constants: &VersionedConstants,
    ) -> GasAmount {
        // Discounted gas converts data gas to L1 gas. Add L2 gas using conversion ratio.
        let discounted_l1_gas = to_discounted_l1_gas(
            gas_prices.l1_gas_price,
            gas_prices.l1_data_gas_price.into(),
            self.l1_gas,
            self.l1_data_gas,
        );
        discounted_l1_gas
            .checked_add(versioned_constants.sierra_gas_to_l1_gas_amount_round_up(self.l2_gas))
            .unwrap_or_else(|| {
                panic!(
                    "L1 gas amount overflowed: addition of converted L2 gas ({}) to discounted \
                     gas ({}) resulted in overflow.",
                    self.l2_gas, discounted_l1_gas
                );
            })
    }
```

**File:** crates/blockifier/src/fee/resources.rs (L32-53)
```rust
impl TransactionResources {
    /// Computes and returns the total gas consumption. The L2 gas amount may be converted
    /// to L1 gas (depending on the gas vector computation mode).
    pub fn to_gas_vector(
        &self,
        versioned_constants: &VersionedConstants,
        use_kzg_da: bool,
        computation_mode: &GasVectorComputationMode,
    ) -> GasVector {
        let starknet_gas = self.starknet_resources.to_gas_vector(
            versioned_constants,
            use_kzg_da,
            computation_mode,
        );
        let computation_gas = self.computation.to_gas_vector(versioned_constants, computation_mode);
        starknet_gas.checked_add(computation_gas).unwrap_or_else(|| {
            panic!(
                "Transaction resources to gas vector overflowed: starknet gas cost is \
                 {starknet_gas:?}, computation gas is {computation_gas:?}",
            )
        })
    }
```

**File:** crates/blockifier/src/bouncer.rs (L662-726)
```rust
        let tx_bouncer_weights = tx_weights.bouncer_weights;

        // Check if the transaction can fit the current block available capacity.
        let err_msg = format!(
            "Addition overflow. Transaction weights: {tx_bouncer_weights:?}, block weights: {:?}.",
            self.get_bouncer_weights()
        );
        let next_accumulated_weights =
            self.get_bouncer_weights().checked_add(tx_bouncer_weights).expect(&err_msg);
        if !self.bouncer_config.has_room(next_accumulated_weights) {
            let exceeded_weights =
                self.bouncer_config.get_exceeded_weights(next_accumulated_weights);
            log::debug!(
                "Transaction cannot be added to the current block, block capacity reached; \
                 transaction weights: {:?}, block weights: {:?}. Block max capacity reached on \
                 fields: {}",
                tx_weights.bouncer_weights,
                self.get_bouncer_weights(),
                exceeded_weights
            );
            // Record the block-full metric only once per block. Later candidate txs that also do
            // not fit (subsequent chunks / executor invocations share this bouncer) would otherwise
            // inflate the counter into a per-rejected-tx count instead of a per-block count.
            if !self.block_full_recorded {
                record_exceeded_bouncer_resources(&exceeded_weights);
                self.block_full_recorded = true;
            }
            Err(TransactionExecutorError::BlockFull)?
        }

        self.update(tx_weights, tx_execution_summary, &marginal_state_changes_keys);

        Ok(())
    }

    fn update(
        &mut self,
        tx_weights: TxWeights,
        tx_execution_summary: &ExecutionSummary,
        state_changes_keys: &StateChangesKeys,
    ) {
        let bouncer_weights = &tx_weights.bouncer_weights;
        let err_msg = format!(
            "Addition overflow. Transaction weights: {bouncer_weights:?}, block weights: {:?}.",
            self.get_bouncer_weights()
        );
        self.accumulated_weights.bouncer_weights = self
            .accumulated_weights
            .bouncer_weights
            .checked_add(tx_weights.bouncer_weights)
            .expect(&err_msg);
        self.accumulated_weights
            .casm_hash_computation_data_sierra_gas
            .extend(tx_weights.casm_hash_computation_data_sierra_gas);
        self.accumulated_weights
            .casm_hash_computation_data_proving_gas
            .extend(tx_weights.casm_hash_computation_data_proving_gas);
        self.visited_storage_entries.extend(&tx_execution_summary.visited_storage_entries);
        // Note: cancelling writes (0 -> 1 -> 0) will not be removed, but it's fine since fee was
        // charged for them.
        // Also, `get_patricia_update_resources` relies on this property - each cell must
        // be counted at most once as modified.
        self.state_changes_keys.extend(state_changes_keys);
        self.accumulated_weights.class_hashes_to_migrate.extend(tx_weights.class_hashes_to_migrate);
    }
```

**File:** crates/blockifier/src/bouncer.rs (L735-745)
```rust
fn vm_resource_to_gas_amount(amount: usize, gas_per_unit: u64, name: &str) -> GasAmount {
    let amount_u64 = u64_from_usize(amount);
    let gas = amount_u64.checked_mul(gas_per_unit).unwrap_or_else(|| {
        panic!(
            "Multiplication overflow converting {name} to gas. units: {amount_u64}, gas per unit: \
             {gas_per_unit}."
        )
    });

    GasAmount(gas)
}
```

**File:** crates/blockifier/src/fee/fee_test.rs (L354-386)
```rust
#[rstest]
#[should_panic(expected = "L1Gas cost overflowed")]
#[case::l1_overflows(u64::MAX, 0, 0)]
#[should_panic(expected = "L1DataGas cost overflowed")]
#[case::l1_data_overflows(0, u64::MAX, 0)]
#[should_panic(expected = "L2Gas cost overflowed")]
#[case::l2_gas_overflows(0, 0, u64::MAX)]
fn test_get_fee_by_gas_vector_overflow(
    #[case] l1_gas: u64,
    #[case] l1_data_gas: u64,
    #[case] l2_gas: u64,
) {
    let huge_gas_price = NonzeroGasPrice::try_from(2_u128 * u128::from(u64::MAX)).unwrap();
    let mut block_info = BlockContext::create_for_account_testing().block_info;
    block_info.gas_prices = GasPrices {
        eth_gas_prices: GasPriceVector {
            l1_gas_price: huge_gas_price,
            l1_data_gas_price: huge_gas_price,
            l2_gas_price: huge_gas_price,
        },
        strk_gas_prices: GasPriceVector {
            l1_gas_price: huge_gas_price,
            l1_data_gas_price: huge_gas_price,
            l2_gas_price: huge_gas_price,
        },
    };
    let gas_vector =
        GasVector { l1_gas: l1_gas.into(), l1_data_gas: l1_data_gas.into(), l2_gas: l2_gas.into() };
    assert_eq!(
        get_fee_by_gas_vector(&block_info, gas_vector, &FeeType::Eth, Tip::ZERO),
        Fee(u128::MAX)
    );
}
```
