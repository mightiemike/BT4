Confirmed: the `paid_fee_on_l1` value comes directly from an untrusted L1 `LogMessageToL2` event's `fee` field (any L1 account can call `sendMessageToL2` with an arbitrary `msg.value`), and the blockifier's `L1HandlerTransaction::execute_raw` only checks `paid_fee != Fee(0)` rather than checking it covers the actual computed fee, matching the analog bug class. [1](#0-0) [2](#0-1) [3](#0-2) 

### Title
Insufficient fee sufficiency check for L1-to-L2 messages allows attacker to consume L2/sequencer resources while paying an arbitrarily low fee - ([File: crates/blockifier/src/transaction/l1_handler_transaction.rs])

### Summary
`L1HandlerTransaction::execute_raw` in the blockifier only verifies that `paid_fee_on_l1 != Fee(0)`, instead of verifying that the fee actually paid on L1 covers the resources consumed by the corresponding L1 handler execution. Because `paid_fee_on_l1` is fully attacker-controlled (it is copied verbatim from the `msg.value`/`fee` field of the `LogMessageToL2` event emitted by an arbitrary L1 caller of the Starknet core contract's `sendMessageToL2`), any unprivileged L1 sender can send a message with the minimal non-zero fee (e.g. 1 wei) while crafting a large payload/calldata that drives execution costs up to the generous `l1_handler_max_amount_bounds` ceiling.

### Finding Description
The relevant code: [4](#0-3) 

After a successful execution, the code enforces only an **upper bound** check (`check_all_gas_amounts_within_bounds` against `l1_handler_max_amount_bounds`, which are set to very large values, e.g. `10000000000` for each resource as seen in the versioned constants file) and then checks `paid_fee == Fee(0)` as the *only* sufficiency criterion for the fee actually paid on L1: [5](#0-4) 

The comment explicitly documents that this insufficiency is known and deferred: "TODO(Arni): Consider removing this check. It is covered by the starknet core contract... For now, assert only that any amount of fee was paid." This means the sequencer/blockifier layer itself performs no linkage between `receipt.fee` (the actual STRK/ETH-equivalent cost of the resources consumed, computed from the real payload size, gas prices, and steps) and the amount paid on L1. The `paid_fee_on_l1` value is sourced directly from an L1 event that any external account can trigger with an arbitrary payload and an arbitrary (even minimal) `msg.value`: [6](#0-5) 

This exactly parallels the referenced Optimism bug class: a lower-bound compensation check that does not scale with the actual resources/data consumed, allowing a caller to pay a negligible fee while forcing the network to process a transaction whose real cost (proportional to payload length, driving `payload_size`-dependent OS steps, L2 gas, and L1 data-gas costs via `TransactionReceipt::from_l1_handler`) is far higher, up to the `l1_handler_max_amount_bounds` ceiling.

### Impact Explanation
An attacker can repeatedly submit L1-to-L2 messages with large calldata payloads (bounded only by the generous `l1_handler_max_amount_bounds`, e.g. `10^10` gas units per resource) while paying only a token nonzero fee on L1. This causes the sequencer to expend L2 execution resources (steps, builtins, data availability) without commensurate compensation, and can be repeated cheaply to degrade sequencer throughput/build capacity — a resource-exhaustion/DoS vector against block building, consistent with "Using L2 resources without enough compensation" and "DoS" impacts in the original report.

### Likelihood Explanation
The path is trivially reachable by any unprivileged L1 account calling the public `sendMessageToL2` function of the Starknet core contract with a large payload and minimal `msg.value`; no special privileges, staking, or node compromise is required. The blockifier logic that fails to validate fee sufficiency executes unconditionally for every scraped L1 handler transaction.

### Recommendation
Enforce that `paid_fee_on_l1` covers (or is proportional to) the actual computed `receipt.fee`/gas vector rather than merely checking non-zero, e.g. reject (or scale down allowed resource consumption for) L1 handler transactions where `paid_fee_on_l1 < receipt.fee` (or a documented minimum tied to payload size), mirroring the original recommendation to tie the lower bound to the size of the transaction's data.

### Proof of Concept
1. An attacker calls the Starknet L1 core contract's `sendMessageToL2` with `msg.value = 1` wei and a large `payload` array (as done for legitimate messages in `send_message_to_l2`, but with attacker-controlled large payload): [7](#0-6) 
2. The L1 events scraper picks up the `LogMessageToL2` event and constructs an `ExecutableL1HandlerTransaction` with `paid_fee_on_l1 = Fee(1)`: [8](#0-7) 
3. During block building, `L1HandlerTransaction::execute_raw` executes the handler; as long as `receipt.gas` stays within `l1_handler_max_amount_bounds` (which is very high), the only fee check performed is `paid_fee != Fee(0)`, which trivially passes with `Fee(1)`, letting the transaction succeed despite consuming resources vastly exceeding the 1 wei paid.

### Citations

**File:** crates/blockifier/src/transaction/l1_handler_transaction.rs (L92-113)
```rust
                // Enforce resource bounds.
                let fee_check_report = FeeCheckReport::check_all_gas_amounts_within_bounds(
                    &l1_handler_bounds,
                    &receipt.gas,
                );
                match fee_check_report {
                    Ok(()) => {
                        // Post-execution check passed, commit the execution.
                        execution_state.commit();
                        // TODO(Arni): Consider removing this check. It is covered by the starknet
                        // core contract.
                        let paid_fee = self.paid_fee_on_l1;
                        // For now, assert only that any amount of fee was paid.
                        // The error message still indicates the required fee.
                        if paid_fee == Fee(0) {
                            return Err(TransactionExecutionError::TransactionFeeError(Box::new(
                                TransactionFeeError::InsufficientFee {
                                    paid_fee,
                                    actual_fee: receipt.fee,
                                },
                            )));
                        }
```

**File:** crates/papyrus_base_layer/src/eth_events.rs (L23-35)
```rust
pub fn parse_event(log: Log, block_timestamp: BlockTimestamp) -> EthereumBaseLayerResult<L1Event> {
    let l1_tx_hash = log.transaction_hash;
    let log = log.inner;

    let event = Starknet::StarknetEvents::decode_log(&log)?.data;

    match event {
        Starknet::StarknetEvents::LogMessageToL2(event) => {
            let fee = Fee(event.fee.try_into().map_err(EthereumBaseLayerError::FeeOutOfRange)?);
            let event_data = EventData::try_from(event)?;
            let tx = L1HandlerTransaction::from(event_data);
            Ok(L1Event::LogMessageToL2 { tx, fee, l1_tx_hash, block_timestamp })
        }
```

**File:** crates/starknet_api/src/executable_transaction.rs (L380-405)
```rust
#[derive(Clone, Debug, Default, Deserialize, Eq, PartialEq, Serialize, Hash)]
pub struct L1HandlerTransaction {
    pub tx: crate::transaction::L1HandlerTransaction,
    pub tx_hash: TransactionHash,
    pub paid_fee_on_l1: Fee,
}

impl L1HandlerTransaction {
    pub const L1_HANDLER_TYPE_NAME: &str = "L1_HANDLER";

    pub fn create(
        raw_tx: crate::transaction::L1HandlerTransaction,
        chain_id: &ChainId,
        paid_fee_on_l1: Fee,
    ) -> Result<L1HandlerTransaction, StarknetApiError> {
        let tx_hash = raw_tx.calculate_transaction_hash(chain_id, &raw_tx.version)?;
        Ok(Self { tx: raw_tx, tx_hash, paid_fee_on_l1 })
    }

    pub fn payload_size(&self) -> usize {
        // The calldata includes the "from" field, which is not a part of the payload.
        // `saturating_sub` guards the empty-calldata case (which would otherwise underflow to
        // `usize::MAX` in release): `L1HandlerTransaction` derives `Deserialize` and `Calldata`
        // has no non-empty invariant.
        self.tx.calldata.0.len().saturating_sub(1)
    }
```

**File:** crates/blockifier/resources/blockifier_versioned_constants_0_13_2_1.json (L362-367)
```json
        "l1_handler_version": 0,
        "l1_handler_max_amount_bounds": {
            "l1_gas": 10000000000,
            "l1_data_gas": 10000000000,
            "l2_gas": 10000000000
        },
```

**File:** crates/apollo_base_layer_tests/src/anvil_base_layer.rs (L284-307)
```rust
pub async fn send_message_to_l2(
    starknet_core_contract: &StarknetL1Contract,
    l1_handler: &L1HandlerTransaction,
) -> TransactionReceipt {
    const PAID_FEE_ON_L1: U256 = U256::from_be_slice(b"paid"); // Arbitrary value.

    let l2_contract_address = l1_handler.contract_address.0.key().to_hex_string().parse().unwrap();
    let l2_entry_point = l1_handler.entry_point_selector.0.to_hex_string().parse().unwrap();

    // The calldata of an L1 handler transaction consists of the L1 sender address followed by
    // the transaction payload. We remove the sender address to extract the message
    // payload.
    let payload =
        l1_handler.calldata.0[1..].iter().map(|x| x.to_hex_string().parse().unwrap()).collect();
    let msg = starknet_core_contract.sendMessageToL2(l2_contract_address, l2_entry_point, payload);

    msg
        // Sets a non-zero fee to be paid on L1.
        .value(PAID_FEE_ON_L1)
        // Sends the transaction to the Starknet L1 contract. For debugging purposes, replace
        // `.send()` with `.call_raw()` to retrieve detailed error messages from L1.
        .send().await.expect("Transaction submission to Starknet L1 contract failed.")
        // Waits until the transaction is received on L1 and then fetches its receipt.
        .get_receipt().await.expect("Transaction was not received on L1 or receipt retrieval failed.")
```

**File:** crates/apollo_l1_events_types/src/lib.rs (L317-320)
```rust
            L1Event::LogMessageToL2 { tx, fee, block_timestamp, .. } => {
                let tx = L1HandlerTransaction::create(tx, chain_id, fee)?;
                Self::L1HandlerTransaction { l1_handler_tx: tx, block_timestamp, scrape_timestamp }
            }
```
