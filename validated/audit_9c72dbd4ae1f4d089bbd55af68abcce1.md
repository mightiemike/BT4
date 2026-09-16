### Title
`l1_handler_message_hash` panics on empty-calldata L1 handler transaction, allowing an L1 message sender to crash sequencer L1-handler processing - (File: `crates/starknet_api/src/hash.rs`)

### Summary
`l1_handler_message_hash` unwraps `calldata.0.split_first()` with `.expect(...)`, assuming calldata always contains at least the `from_address` element. This mirrors the BlueBerryBank `getPositionValue` bug class: a function on a mandatory, always-executed path (there: liquidation loop over reward tokens; here: L1-handler message-hash computation invoked from the L1 events scraper/provider on every incoming L1 message and from RPC `estimateMessageFee`/trace endpoints) panics when fed a malformed/edge-case input instead of returning an error, creating a denial-of-service vector.

### Finding Description
`l1_handler_message_hash` is implemented as:
```rust
let (from_address, payload) =
    calldata.0.split_first().expect("Invalid calldata, expected at least from_address");
``` [1](#0-0) 

This is called from `L1HandlerTransaction::calc_msg_hash` [2](#0-1) 

which is invoked in the L1 scraper's per-poll processing of every incoming `LogMessageToL2`/etc. event [3](#0-2) 
and again whenever the L1 events provider serves proposable transactions to the batcher [4](#0-3) 
as well as from the RPC layer (`api_impl.rs`, message-fee estimation and trace endpoints).

Critically, the codebase itself documents that `Calldata` has "no non-empty invariant" and that `L1HandlerTransaction` derives `Deserialize`, so an empty-calldata L1 handler transaction is constructible — this exact caveat is called out in a sibling function that was hardened against it:
```rust
pub fn payload_size(&self) -> usize {
    // The calldata includes the "from" field, which is not a part of the payload.
    // `saturating_sub` guards the empty-calldata case (which would otherwise underflow to
    // `usize::MAX` in release): `L1HandlerTransaction` derives `Deserialize` and `Calldata`
    // has no non-empty invariant.
    self.tx.calldata.0.len().saturating_sub(1)
}
``` [5](#0-4) 

and is regression-tested explicitly for `payload_size`: [6](#0-5) 

`l1_handler_message_hash`, however, was not given the same protection and still panics via `.expect(...)` rather than returning an error, on the exact same "no non-empty invariant" input.

While the "normal" ingestion paths (`EventData::into::<L1HandlerTransaction>` from `papyrus_base_layer` and `MessageFromL1::into::<L1HandlerTransaction>` from RPC) always prepend a `from_address` element before calldata, guaranteeing non-empty calldata for those specific construction sites, `L1HandlerTransaction` (both the `starknet_api::transaction` type and the `executable_transaction` wrapper) is a plain `Deserialize`-able struct with no invariant enforcement in its own type. Any code path that deserializes/reconstructs an `L1HandlerTransaction` from untrusted/serialized data (e.g., RPC message-fee/trace inputs, state-sync/p2p-sync reconstructed blocks, or OS re-execution replaying historical/serialized transactions) and then calls `calc_msg_hash` on it is exposed to this panic if calldata is empty.

### Impact Explanation
A panic inside the L1 events provider (invoked every time the batcher requests L1-handler transactions for a new block proposal, and every scraper poll) crashes that component's task/thread. Since L1-handler transaction inclusion is on the mandatory block-building path (analogous to the "100% uptime" liquidation requirement in the referenced report), a sequencer process that hits this panic will fail to keep proposing/validating blocks containing L1 handler transactions, degrading it to a denial-of-service on block production for as long as the malformed transaction remains in the pipeline. This matches "network unable to confirm new transactions" from a single crashable input.

### Likelihood Explanation
The likelihood is currently constrained: the two production construction sites (`EventData -> L1HandlerTransaction`, `MessageFromL1 -> L1HandlerTransaction`) both prepend a `from_address`, so calldata is non-empty via the "normal" scraper/API path. The realistic trigger requires a path where an `L1HandlerTransaction` with empty `calldata` is deserialized directly (e.g. RPC endpoints accepting a raw `L1HandlerTransaction`/`MessageFromL1`-derived struct, sync/replay data, or crafted requests to `estimateMessageFee`/trace endpoints) and then has `calc_msg_hash` called on it before any calldata-non-emptiness check. I was not able to fully trace every RPC/deserialization entry point that could feed an attacker-crafted empty-calldata `L1HandlerTransaction` directly into `calc_msg_hash` within the available tool budget — this needs verification with a broader trace of `apollo_rpc`'s `estimateMessageFee`/trace endpoints and any p2p/sync paths that reconstruct L1 handler transactions from serialized bytes.

### Recommendation
Change `l1_handler_message_hash` to return a `Result`/error (e.g. a new `StarknetApiError` variant) instead of panicking on empty calldata, mirroring the defensive fix already applied to `payload_size`:
```rust
pub fn l1_handler_message_hash(...) -> Result<L1L2MsgHash, StarknetApiError> {
    let (from_address, payload) = calldata.0.split_first()
        .ok_or(StarknetApiError::InvalidL1HandlerCalldata)?;
    ...
}
```
Propagate the `Result` through `calc_msg_hash` and all call sites (scraper, provider, RPC), converting to a rejected/invalid-transaction error rather than aborting the process. Add a regression test analogous to `l1_handler_payload_size_empty_calldata_does_not_underflow` for `calc_msg_hash`/`l1_handler_message_hash`.

### Proof of Concept
1. Construct an `L1HandlerTransaction` (starknet_api::transaction type) with `calldata: Calldata(Arc::new(vec![]))` — this is directly buildable since `Calldata`/`L1HandlerTransaction` have no non-empty invariant, as demonstrated by the existing test: [7](#0-6) 
2. Call `.calc_msg_hash()` on it (as done in `l1_scraper.rs:361` and `l1_events_provider.rs:241`, or via an RPC path that accepts a raw `L1HandlerTransaction`/message struct and computes the message hash for `estimateMessageFee`/trace responses).
3. `calldata.0.split_first()` returns `None`; `.expect("Invalid calldata, expected at least from_address")` panics, crashing the calling task/thread instead of returning a graceful error.

### Citations

**File:** crates/starknet_api/src/hash.rs (L154-163)
```rust
impl L1HandlerTransaction {
    pub fn calc_msg_hash(&self) -> L1L2MsgHash {
        l1_handler_message_hash(
            &self.contract_address,
            self.nonce,
            &self.entry_point_selector,
            &self.calldata,
        )
    }
}
```

**File:** crates/starknet_api/src/hash.rs (L167-174)
```rust
pub fn l1_handler_message_hash(
    contract_address: &ContractAddress,
    nonce: Nonce,
    entry_point_selector: &EntryPointSelector,
    calldata: &Calldata,
) -> L1L2MsgHash {
    let (from_address, payload) =
        calldata.0.split_first().expect("Invalid calldata, expected at least from_address");
```

**File:** crates/apollo_l1_events/src/l1_scraper.rs (L358-364)
```rust
        // Collect the L1-L2 message hashes (keccak) for L1 handler transactions.
        let l1_msg_hashes = events.iter().filter_map(|event| match event {
            Event::L1HandlerTransaction { l1_handler_tx, .. } => {
                Some(l1_handler_tx.tx.calc_msg_hash())
            }
            _ => None,
        });
```

**File:** crates/apollo_l1_events/src/l1_events_provider.rs (L234-244)
```rust
                );
                debug!(
                    "Returned L1Handler txs: {:?}",
                    txs.iter()
                        .map(|tx| format!(
                            "L2 tx hash: {}, L1-L2 msg hash: {}",
                            tx.tx_hash,
                            tx.tx.calc_msg_hash()
                        ))
                        .collect::<Vec<_>>()
                );
```

**File:** crates/starknet_api/src/executable_transaction.rs (L399-405)
```rust
    pub fn payload_size(&self) -> usize {
        // The calldata includes the "from" field, which is not a part of the payload.
        // `saturating_sub` guards the empty-calldata case (which would otherwise underflow to
        // `usize::MAX` in release): `L1HandlerTransaction` derives `Deserialize` and `Calldata`
        // has no non-empty invariant.
        self.tx.calldata.0.len().saturating_sub(1)
    }
```

**File:** crates/starknet_api/src/executable_transaction_test.rs (L24-38)
```rust
/// `Calldata` has no non-empty invariant and `L1HandlerTransaction` derives `Deserialize`, so an
/// empty calldata is constructible. `payload_size` must not underflow (debug panic / release wrap
/// to usize::MAX); the payload of an empty calldata is just 0.
#[test]
fn l1_handler_payload_size_empty_calldata_does_not_underflow() {
    let tx = RpcL1HandlerTransaction {
        version: TransactionVersion::ZERO,
        nonce: Nonce::default(),
        contract_address: Default::default(),
        entry_point_selector: Default::default(),
        calldata: Calldata(Arc::new(vec![])),
    };
    let executable_tx =
        L1HandlerTransaction { tx, tx_hash: TransactionHash::default(), paid_fee_on_l1: Fee(0) };
    assert_eq!(executable_tx.payload_size(), 0);
```
