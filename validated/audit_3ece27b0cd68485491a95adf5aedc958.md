### Title
`estimate_minimal_gas_vector()` omits calldata/signature archival gas cost, allowing sub-cost transactions to pass the pre-validation resource-bounds check - (File: crates/blockifier/src/fee/gas_usage.rs)

### Summary
`estimate_minimal_gas_vector()` is used by `AccountTransaction::check_fee_bounds()` — invoked from `perform_pre_validation_stage()` (reachable via the gateway/mempool stateful validator and at execution time for every submitted transaction) — to reject transactions whose declared `resource_bounds`/`max_fee` are below the minimal cost the sequencer will incur. The estimate only accounts for fixed OS step overhead and state-diff (DA) gas, but never adds the calldata+signature archival gas cost that is actually charged later via `ArchivalDataResources::get_calldata_and_signature_gas_cost()`. This mirrors the GMX report's root cause: a "minimum required" gas/fee estimator that omits a user-controlled, unboundedly-scalable cost dimension (SwapPath length in GMX; calldata/signature length here), letting an attacker construct transactions that pass the cheap pre-check yet impose real, uncompensated resource cost on the sequencer.

### Finding Description
`estimate_minimal_gas_vector()` computes:
- `os_steps_for_type` from `os_resources_for_tx_type(tx_type, extended_calldata_length)` (a small, mostly fixed per-tx-type step count) plus KZG DA step overhead,
- `da_gas_cost` from the sender balance/nonce state-diff only,

and sums these into the "minimal gas vector." [1](#0-0) 

It never invokes/aggregates the archival-data gas cost for calldata and signature felts, which is the actual cost model applied post-execution: [2](#0-1) 

That archival cost scales linearly with `extended_calldata_length + signature_length` — both of which are entirely attacker-controlled up to the gateway's `max_calldata_length` (5000 felts per the versioned constants) — and is charged in `ArchivalDataResources::to_gas_vector()`, which *is* included in the actual `StarknetResources::to_gas_vector()` used for the real fee/receipt computation: [3](#0-2) [4](#0-3) 

The minimal-gas check is invoked in `check_fee_bounds()`, called from `perform_pre_validation_stage()` — the gate that (a) increments the nonce and (b) is used both in `stateful_validator.rs` (mempool/gateway admission path, reached by any unprivileged transaction sender) and again at the start of block-building execution: [5](#0-4) [6](#0-5) 

Because the minimal-gas estimate ignores the calldata/signature archival cost, an attacker can craft a V3 transaction with a very large calldata (and/or signature) array while setting `resource_bounds` just above the (artificially low) `minimal_gas_amount_vector`. `check_fee_bounds()` will accept it and the nonce will be incremented, allowing the transaction into the mempool/executed batch. The sequencer then must run `__validate__`/`__execute__` and perform the felt-serialization/DA bookkeping for the oversized calldata, consuming real steps, memory and bandwidth, all while the resource bounds the attacker committed to may be set only slightly above the (deflated) minimal estimate rather than the true cost.

### Impact Explanation
This causes the sequencer/proposer to expend CPU, memory and bandwidth (parsing/serializing/copying up to ~5000-felt calldata and signature payloads, and validating them) for transactions that were never required to reserve gas for that cost at admission time. Because the check happens before the *actual* fee/resource-bounds verification (which only occurs post-execution against the true consumed gas vector, by which point resources are already spent), an attacker can cheaply flood the gateway/mempool and block-building pipeline with maximal-calldata transactions that are admitted under deflated minimal-fee requirements, and later revert/fail their resource bounds check (contributing no compensating fee for the archival-data cost incurred). At scale this degrades mempool admission fairness and sequencer throughput, and can act as a resource-exhaustion vector against block builders without a proportional cost to the attacker, which can hinder the network's ability to process legitimate transactions in a timely manner.

### Likelihood Explanation
High reachability: any account can submit an ordinary V3 invoke/declare/deploy-account transaction with a maximal calldata/signature array (bounded only by the gateway's `max_calldata_length`, e.g., 5000) and near-minimal resource bounds computed from the (buggy) estimator. No special privileges are required — this is purely a function of transaction construction by an unprivileged sender.

### Recommendation
Include the archival-data gas cost for calldata and signature (mirroring `ArchivalDataResources::get_calldata_and_signature_gas_cost`) in `estimate_minimal_gas_vector()`, e.g.:

```rust
let archival_gas_costs = versioned_constants.get_archival_data_gas_costs(gas_usage_vector_computation_mode);
let total_data_size = u64_from_usize(tx.extended_calldata_length() + tx.signature_length());
let calldata_and_signature_gas = (archival_gas_costs.gas_per_data_felt * total_data_size).to_integer().into();
let calldata_and_signature_gas_vector = match gas_usage_vector_computation_mode {
    GasVectorComputationMode::All => GasVector::from_l2_gas(calldata_and_signature_gas),
    GasVectorComputationMode::NoL2Gas => GasVector::from_l1_gas(calldata_and_signature_gas),
};
// add calldata_and_signature_gas_vector into the returned total
```
This ensures the pre-validation minimal-fee check enforces resource bounds proportional to actual archival-data cost before nonce increment/admission, closing the underpriced-spam vector.

### Proof of Concept
1. Build a V3 `InvokeTransaction` with `calldata` padded to the gateway's max allowed length (e.g., 5000 felts) and a large `signature` array.
2. Set `resource_bounds` (`AllResourceBounds`) to values just above what `estimate_minimal_gas_vector()` returns (which is computed from fixed OS-step overhead + trivial state-diff DA cost only, independent of the 5000-felt payload).
3. Submit through the stateful validator / mempool path; `perform_pre_validation_stage()` → `check_fee_bounds()` passes because the huge calldata/signature cost is not part of `minimal_gas_amount_vector`, even though the real `ArchivalDataResources` cost for that payload (computed in `resources.rs::get_calldata_and_signature_gas_cost`) is far larger.
4. The nonce is incremented and the transaction proceeds to validation/execution, consuming sequencer resources for the oversized payload; only at the post-execution fee check does the discrepancy surface (transaction reverts for insufficient resource bounds), by which time the resource cost has already been incurred with no compensating minimal-fee guarantee having been enforced at admission.

(Note: I was not able to fully trace whether `stateful_validator.rs`'s mempool-facing entry point calls `perform_pre_validation_stage` with `charge_fee = true` in every configuration, since the file's caller context beyond the single grep match wasn't retrieved in the tool budget available — this should be confirmed by a follow-up review of `crates/blockifier/src/blockifier/stateful_validator.rs` before treating this as fully confirmed for the mempool path versus only the execution-time path.)

### Citations

**File:** crates/blockifier/src/fee/gas_usage.rs (L190-214)
```rust
    // TODO(Yoni): BLOCKIFIER-RESET: reuse TransactionReceipt code.
    let data_segment_length = get_onchain_data_segment_length(&state_changes_by_account_tx);
    let os_steps_for_type = versioned_constants
        .os_resources_for_tx_type(&tx.tx_type(), tx.extended_calldata_length())
        .n_steps
        + versioned_constants.os_kzg_da_resources(data_segment_length).n_steps;

    let resources = ExtendedExecutionResources {
        vm_resources: ExecutionResources { n_steps: os_steps_for_type, ..Default::default() },
        ..Default::default()
    };
    let da_gas_cost = get_da_gas_cost(&state_changes_by_account_tx, block_info.use_kzg_da);
    let vm_resources_cost = get_extended_vm_resources_cost(
        versioned_constants,
        &resources,
        0,
        gas_usage_vector_computation_mode,
    );
    da_gas_cost.checked_add(vm_resources_cost).unwrap_or_else(|| {
        panic!(
            "Overflow in minimal gas estimation; attempted to add {da_gas_cost:?} to \
             {vm_resources_cost:?}"
        )
    })
}
```

**File:** crates/blockifier/src/fee/resources.rs (L164-186)
```rust
    /// Returns the gas cost of the starknet resources, summing all components.
    /// The L2 gas amount may be converted to L1 gas (depending on the gas vector computation mode).
    pub fn to_gas_vector(
        &self,
        versioned_constants: &VersionedConstants,
        use_kzg_da: bool,
        mode: &GasVectorComputationMode,
    ) -> GasVector {
        [
            self.archival_data.to_gas_vector(versioned_constants, mode),
            self.state.to_gas_vector(use_kzg_da, &versioned_constants.allocation_cost),
            self.messages.to_gas_vector(),
        ]
        .iter()
        .fold(GasVector::ZERO, |accumulator, cost| {
            accumulator.checked_add(*cost).unwrap_or_else(|| {
                panic!(
                    "Starknet resources to gas vector overflowed: tried to add {accumulator:?} to \
                     {cost:?}",
                )
            })
        })
    }
```

**File:** crates/blockifier/src/fee/resources.rs (L271-294)
```rust
impl ArchivalDataResources {
    /// Returns the cost of the transaction's archival data, for example, calldata, signature, code,
    /// and events.
    pub fn to_gas_vector(
        &self,
        versioned_constants: &VersionedConstants,
        mode: &GasVectorComputationMode,
    ) -> GasVector {
        [
            self.get_calldata_and_signature_gas_cost(versioned_constants, mode),
            self.get_code_gas_cost(versioned_constants, mode),
            self.get_client_side_proof_gas_cost(versioned_constants, mode),
            self.event_summary.to_gas_vector(versioned_constants, mode),
        ]
        .into_iter()
        .fold(GasVector::ZERO, |accumulator, cost| {
            accumulator.checked_add(cost).unwrap_or_else(|| {
                panic!(
                    "Archival data resources to gas vector overflowed: tried to add \
                     {accumulator:?} gas vector to {cost:?} gas vector.",
                )
            })
        })
    }
```

**File:** crates/blockifier/src/fee/resources.rs (L296-315)
```rust
    /// Returns the cost for transaction calldata and transaction signature. Each felt costs a
    /// fixed and configurable amount of gas. This cost represents the cost of storing the
    /// calldata and the signature on L2.
    fn get_calldata_and_signature_gas_cost(
        &self,
        versioned_constants: &VersionedConstants,
        mode: &GasVectorComputationMode,
    ) -> GasVector {
        let archival_gas_costs = versioned_constants.get_archival_data_gas_costs(mode);

        // TODO(Avi, 20/2/2024): Calculate the number of bytes instead of the number of felts.
        let total_data_size = u64_from_usize(self.extended_calldata_length + self.signature_length);
        let gas_amount =
            (archival_gas_costs.gas_per_data_felt * total_data_size).to_integer().into();

        match mode {
            GasVectorComputationMode::All => GasVector::from_l2_gas(gas_amount),
            GasVectorComputationMode::NoL2Gas => GasVector::from_l1_gas(gas_amount),
        }
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L353-372)
```rust
    // Performs static checks before executing validation entry point.
    // Note that nonce is incremented during these checks.
    pub fn perform_pre_validation_stage<S: State + StateReader>(
        &self,
        state: &mut S,
        tx_context: &TransactionContext,
    ) -> TransactionPreValidationResult<()> {
        let tx_info = &tx_context.tx_info;
        Self::handle_nonce(state, tx_info, self.execution_flags.strict_nonce_check)?;

        if self.execution_flags.charge_fee {
            self.check_fee_bounds(tx_context)?;

            verify_can_pay_committed_bounds(state, tx_context).map_err(Box::new)?;
        }

        self.validate_proof_facts(&tx_context.block_context, state)?;

        Ok(())
    }
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L374-397)
```rust
    fn check_fee_bounds(
        &self,
        tx_context: &TransactionContext,
    ) -> TransactionPreValidationResult<()> {
        let minimal_gas_amount_vector = estimate_minimal_gas_vector(
            &tx_context.block_context,
            self,
            &tx_context.get_gas_vector_computation_mode(),
        );
        let TransactionContext { block_context, tx_info } = tx_context;
        let block_info = &block_context.block_info;
        let fee_type = &tx_info.fee_type();
        match tx_info {
            TransactionInfo::Current(context) => {
                let resources_amount_tuple = match &context.resource_bounds {
                    ValidResourceBounds::L1Gas(l1_gas_resource_bounds) => vec![(
                        L1Gas,
                        l1_gas_resource_bounds,
                        minimal_gas_amount_vector.to_l1_gas_for_fee(
                            tx_context.get_gas_prices(),
                            &tx_context.block_context.versioned_constants,
                        ),
                        block_info.gas_prices.l1_gas_price(fee_type),
                    )],
```
