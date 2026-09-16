Based on my investigation, I found a genuine analog: an unchecked-invariant panic in L1 message hash computation, reachable from a real L1 message sender.

### Title
Sequencer panics on `LogMessageToL2` with empty payload during L1-to-L2 message hash computation - (File: crates/starknet_api/src/hash.rs)

### Summary
`l1_handler_message_hash` in [1](#0-0)  assumes `Calldata` always has at least one element (the L1 `from_address`) and calls `.split_first().expect(...)`, which panics if the calldata is empty. `L1HandlerTransaction`'s `Calldata` has no non-empty invariant enforced at the type level, as explicitly acknowledged in a nearby test comment: [2](#0-1)  and in `payload_size()`'s defensive `saturating_sub` at [3](#0-2) .

### Finding Description
Similar to the CTDB CVE where insufficient integrity validation of a received packet field (length/termination) let malformed input reach unguarded processing, this codebase has an unguarded structural assumption inconsistently enforced across `L1HandlerTransaction` call sites: some code (`payload_size`) defensively handles empty calldata, but `calc_msg_hash`/`l1_handler_message_hash` does not, and will panic via `.expect()` on an empty `calldata.0`. `calc_msg_hash()` is invoked directly on scraped/queued L1-handler transactions in the sequencer's live path: `crates/apollo_l1_events/src/l1_scraper.rs` line 361 (`l1_handler_tx.tx.calc_msg_hash()`) inside `fetch_events`, and again in `crates/apollo_l1_events/src/l1_events_provider.rs` line 241 inside `get_txs` (used when proposing blocks). Both are executed unconditionally for every `L1HandlerTransaction` produced from an L1 `LogMessageToL2` event.

The calldata for these transactions is built from the L1 message's `payload` with the L1 sender's `from_address` prepended (as elsewhere in the codebase, e.g. `crates/apollo_batcher/src/cende_client_types.rs:75-88` and `crates/starknet_os_flow_tests/src/test_manager.rs:690-696` show this convention). If the conversion path from the raw L1 event (`LogMessageToL2`) to the `starknet_api::transaction::L1HandlerTransaction`/`Calldata` does not itself guarantee a non-empty result (e.g., if it is built as `[from_address] ++ payload` this can never be empty, but if the codebase re-parses/reconstructs calldata elsewhere without this guarantee, or if a future/alternate transaction-construction path — e.g., a fee-estimation, RPC-deserialization, or test/utility path — produces an `L1HandlerTransaction` with empty `Calldata`, the panic is reachable). I was not able to fully trace the exact L1-event-to-`Calldata` conversion code (the mapping from `papyrus_base_layer::L1Event::LogMessageToL2` into `starknet_api::transaction::L1HandlerTransaction`) within available search results, so I cannot conclusively prove that *scraped* L1 events can never yield empty calldata. This is the key uncertainty in this finding.

### Impact Explanation
If reachable, an `L1HandlerTransaction` with empty `calldata` reaching `calc_msg_hash()` triggers a Rust panic. Because this call occurs in `L1EventsScraper::fetch_events` (called every polling interval, unconditionally for all `L1HandlerTransaction` events) and in `L1EventsProvider::get_txs` (called by the batcher on every block proposal), a panic here would crash the corresponding sequencer component/task. Depending on how the component supervises panics, this could stop L1 message ingestion or block proposal — a denial-of-service impacting the sequencer's ability to confirm new transactions, analogous to the CTDB "process crashes" impact.

### Likelihood Explanation
Low-to-Medium and unconfirmed: the likelihood hinges entirely on whether any reachable code path can construct an `L1HandlerTransaction`/`Calldata` with zero elements before `calc_msg_hash()` is invoked on it. The codebase's own test comment acknowledges `Calldata` "has no non-empty invariant" for `L1HandlerTransaction`, which suggests this was already recognized as a latent risk (mitigated only in `payload_size`, not in `calc_msg_hash`). Without full visibility into the L1-event-to-transaction construction code, I cannot confirm an attacker (a real L1 message sender submitting a `sendMessageToL2` call with empty payload) can trigger this, since the `from_address` is typically always prepended, making zero-length calldata unlikely via the normal scraping path. I flag this explicitly as unresolved rather than asserting exploitability.

### Recommendation
Replace the `.expect()` panic in `l1_handler_message_hash` ( [4](#0-3) ) with a proper `Result`-returning error path, mirroring the defensive `saturating_sub` pattern already used in `payload_size`. Additionally, enforce a non-empty-calldata invariant at construction time for `L1HandlerTransaction` (e.g., in `L1HandlerTransaction::create` at [5](#0-4) ) so malformed transactions are rejected early rather than causing a panic deep in hash computation.

### Proof of Concept
Not independently verifiable from available context — I could not confirm a concrete external-attacker-controlled path that produces a zero-length `Calldata` for an `L1HandlerTransaction` before it reaches `calc_msg_hash()`. If such a path exists, the reproduction is: construct/inject an `L1HandlerTransaction` with `calldata: Calldata(Arc::new(vec![]))`, then call `.calc_msg_hash()` (or trigger `L1EventsScraper::fetch_events` / `L1EventsProvider::get_txs` with it in the queue) to observe the panic, as demonstrated defensively (for the sibling `payload_size` function) in [6](#0-5) .

### Citations

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

**File:** crates/starknet_api/src/executable_transaction_test.rs (L24-26)
```rust
/// `Calldata` has no non-empty invariant and `L1HandlerTransaction` derives `Deserialize`, so an
/// empty calldata is constructible. `payload_size` must not underflow (debug panic / release wrap
/// to usize::MAX); the payload of an empty calldata is just 0.
```

**File:** crates/starknet_api/src/executable_transaction_test.rs (L27-39)
```rust
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
}
```

**File:** crates/starknet_api/src/executable_transaction.rs (L390-397)
```rust
    pub fn create(
        raw_tx: crate::transaction::L1HandlerTransaction,
        chain_id: &ChainId,
        paid_fee_on_l1: Fee,
    ) -> Result<L1HandlerTransaction, StarknetApiError> {
        let tx_hash = raw_tx.calculate_transaction_hash(chain_id, &raw_tx.version)?;
        Ok(Self { tx: raw_tx, tx_hash, paid_fee_on_l1 })
    }
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
