### Title
L1EventsScraper permanently halts on a single malformed/unexpected L1 message, causing persistent denial of L1→L2 message processing - (File: `crates/apollo_l1_events/src/l1_scraper.rs`)

### Summary
`L1EventsScraper::run()` treats only `BaseLayerError` and `L1ReorgDetected` as recoverable conditions in its steady-state polling loop; every other error variant (`HashCalculationError`, `NeedsRestart`, `LatestBlockNumberTooLow`, `LatestL1BlockNumberNoBlockFound`, `NetworkError`) is propagated out of `run()` and then turned into a hard `panic!` by `ComponentStarter::start()`. Because a single attacker-influenced L1 event that fails to convert (`Event::from_l1_event`) or hash (`calc_msg_hash`) surfaces exactly this class of non-`BaseLayerError`, an L1 sender can permanently stop the scraper — the component responsible for ingesting L1→L2 messages — with no automatic, safe recovery path, exactly mirroring the CoreWCF Kafka-tombstone bug class (a single malformed queued item halts the whole consume pump).

### Finding Description
`L1EventsScraper::run()`'s steady-state loop is:
```rust
match self.send_events_to_l1_events_provider().await {
    Err(L1EventsScraperError::BaseLayerError(e)) => { /* warn + continue */ }
    Ok(_) => { /* success metrics */ }
    Err(e @ L1EventsScraperError::L1ReorgDetected { .. }) => { warn!(...); return Err(e); }
    Err(e) => return Err(e),
}
``` [1](#0-0) 

Any error other than a transient RPC/base-layer failure or a detected reorg is treated as fatal for the whole loop, and the `ComponentStarter` wrapper converts that fatal `Result` into a `panic!`:
```rust
async fn start(&mut self) {
    ...
    self.run().await.unwrap_or_else(|e| panic!("Runtime Error: {e}"))
}
``` [2](#0-1) 

`fetch_events()` converts every scraped raw `L1Event` into a Starknet `Event` via `Event::from_l1_event`, which can fail with a `StarknetApiError` wrapped as `L1EventsScraperError::HashCalculationError` — a variant NOT handled by the retry/continue arms above, so it propagates fatally:
```rust
let events = l1_events.into_iter().map(|event| {
    Event::from_l1_event(&self.config.chain_id, event, scrape_timestamp)
        .map_err(L1EventsScraperError::HashCalculationError)
}).collect::<L1EventsScraperResult<Vec<_>, _>>()?;
``` [3](#0-2) 

`fetch_events()` also unconditionally computes a keccak message hash for every scraped `L1HandlerTransaction` event via `calc_msg_hash()`:
```rust
let l1_msg_hashes = events.iter().filter_map(|event| match event {
    Event::L1HandlerTransaction { l1_handler_tx, .. } => Some(l1_handler_tx.tx.calc_msg_hash()),
    _ => None,
});
``` [4](#0-3) 

`calc_msg_hash()` itself is not a `Result` — it unconditionally splits the calldata and **panics** if calldata is empty:
```rust
pub fn calc_msg_hash(&self) -> L1L2MsgHash {
    l1_handler_message_hash(&self.contract_address, self.nonce, &self.entry_point_selector, &self.calldata)
}
...
let (from_address, payload) =
    calldata.0.split_first().expect("Invalid calldata, expected at least from_address");
``` [5](#0-4) 

The scraper's own test comments confirm this fragility is a known invariant the code relies on rather than defends against ("calldata must lead with a from_address for msg-hash calc"): [6](#0-5) 

Whichever of these two paths is hit (a `StarknetApiError` from event conversion surfacing as `HashCalculationError`, or a direct panic in `calc_msg_hash`), the result is the same: the polling loop that would otherwise continue to ingest subsequent, valid L1 messages instead aborts. This is architecturally identical to the reported class: a consumer that must process an unbounded stream of externally-produced records has no per-record error isolation, so one bad record kills the pump for all future good records.

At the node-server layer, the scraper server is combined into a single `FuturesUnordered` alongside all other wrapper servers and driven from one task rather than being individually supervised/restarted per component: [7](#0-6) 
There is no mechanism shown that skips the single bad L1 event, retries with backoff, or isolates the failure to just the scraper — the failure is fatal and requires operator intervention to recover, matching the "permanent" characterization in the advisory.

### Impact Explanation
The L1 events scraper is the sequencer's sole ingestion path for L1→L2 messages (`sendMessageToL2`, cancellations, etc.). If it halts, no `L1HandlerTransaction` can be produced from new L1 activity, and pending cross-layer message cancellations/consumptions also stall — a form of the "network unable to confirm new transactions" impact for the L1↔L2 messaging subsystem. Because the trigger is a fatal `panic!`/task-loop termination with no automatic remediation for the underlying condition (the malformed event is still the next thing that would be re-scraped after any restart, since the block range isn't skipped), this can be a durable/persistent denial of service rather than a transient blip, consistent with CWE-248/CWE-754/CWE-755 (uncaught exception / improper error handling causing halted processing).

### Likelihood Explanation
The precondition mirrors the advisory precisely: anyone able to call the L1 messaging contract's permissionless entry points (`sendMessageToL2`, `startL1ToL2MessageCancellation`) is an "L1 message sender," which the rules explicitly keep in scope as an unprivileged attacker. No special privileges, staking, or node compromise are required — only the ability to submit an L1 transaction with a crafted payload/shape that the event-conversion or msg-hash code cannot handle gracefully. The exact byte-level trigger for either `HashCalculationError` or the `calc_msg_hash` panic depends on details of the L1 log decoding (which builds `calldata = [from_address, ...payload]`) that were not fully confirmed within available context — so while the overall error-handling gap (uncaught fatal errors terminating the ingestion loop) is clearly demonstrated in the code, I could not fully verify from the indexed files whether the base-layer log decoder guarantees non-empty calldata/valid version fields in every code path (e.g., for cancellation-related events specifically). This should be validated with the full source before treating the panic path as unconditionally reachable.

### Recommendation
In `L1EventsScraper::run()`, do not propagate arbitrary per-event conversion/hashing errors as fatal for the whole polling loop; instead, log-and-skip (or retry with the offending event quarantined) individual malformed L1 events, matching the resilience already given to `BaseLayerError`. Additionally, replace the `expect`/panic in `l1_handler_message_hash` (`crates/starknet_api/src/hash.rs`) with a fallible `Result`-returning check so malformed/empty calldata cannot panic the calling task, and ensure `Event::from_l1_event` failures in `fetch_events` are similarly non-fatal to the scraper loop.

### Proof of Concept
1. An L1 sender calls the messaging contract with a crafted payload/cancellation request such that the decoded `L1Event` yields an `L1HandlerTransaction`/cancelled-tx whose fields cause either:
   - `Event::from_l1_event` → `L1HandlerTransaction::create`/`calculate_transaction_hash` to return `Err(StarknetApiError)`, surfaced as `L1EventsScraperError::HashCalculationError`, or
   - `calc_msg_hash()` → `l1_handler_message_hash` to be invoked on empty `calldata`, panicking via the `.expect(...)` in `crates/starknet_api/src/hash.rs:174`.
2. `fetch_events()` (called every polling interval from `send_events_to_l1_events_provider()`) processes this event and returns the fatal error / panics.
3. `run()`'s match arms only special-case `BaseLayerError` and `L1ReorgDetected`; any other error is returned from `run()`.
4. `ComponentStarter::start()` converts that `Err` into `panic!("Runtime Error: {e}")`, terminating the scraper's task/loop.
5. No mechanism re-scrapes around or discards only the bad event — the node operator must intervene, and until then all subsequent legitimate L1→L2 messages are never ingested.

### Citations

**File:** crates/apollo_l1_events/src/l1_scraper.rs (L100-120)
```rust
        // This is the main (steady state) loop.
        loop {
            // Sleep at start of loop, as we get here right after successful initialize+break.
            sleep(self.config.polling_interval_seconds).await;

            match self.send_events_to_l1_events_provider().await {
                Err(L1EventsScraperError::BaseLayerError(e)) => {
                    L1_MESSAGE_SCRAPER_BASELAYER_ERROR_COUNT.increment(1);
                    warn!("BaseLayerError during scraping: {e:?}");
                }
                Ok(_) => {
                    L1_MESSAGE_SCRAPER_SUCCESS_COUNT.increment(1);
                    set_unix_now_seconds(&L1_MESSAGE_SCRAPER_LAST_SUCCESS_TIMESTAMP_SECONDS);
                }
                Err(e @ L1EventsScraperError::L1ReorgDetected { .. }) => {
                    warn!("L1 reorg detected during scraping: {e}");
                    return Err(e);
                }
                Err(e) => return Err(e),
            }
        }
```

**File:** crates/apollo_l1_events/src/l1_scraper.rs (L346-352)
```rust
        let events = l1_events
            .into_iter()
            .map(|event| {
                Event::from_l1_event(&self.config.chain_id, event, scrape_timestamp)
                    .map_err(L1EventsScraperError::HashCalculationError)
            })
            .collect::<L1EventsScraperResult<Vec<_>, _>>()?;
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

**File:** crates/apollo_l1_events/src/l1_scraper.rs (L472-480)
```rust
#[async_trait]
impl<BaseLayerType: BaseLayerContract + Send + Sync + Debug> ComponentStarter
    for L1EventsScraper<BaseLayerType>
{
    async fn start(&mut self) {
        info!("Starting component {}.", type_name::<Self>());
        register_scraper_metrics();
        self.run().await.unwrap_or_else(|e| panic!("Runtime Error: {e}"))
    }
```

**File:** crates/starknet_api/src/hash.rs (L154-176)
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

/// Calculating the message hash of L1 -> L2 message.
/// For more info: <https://docs.starknet.io/documentation/architecture_and_concepts/Network_Architecture/messaging-mechanism/#structure_and_hashing_l1-l2>
pub fn l1_handler_message_hash(
    contract_address: &ContractAddress,
    nonce: Nonce,
    entry_point_selector: &EntryPointSelector,
    calldata: &Calldata,
) -> L1L2MsgHash {
    let (from_address, payload) =
        calldata.0.split_first().expect("Invalid calldata, expected at least from_address");

    let mut encoded = Vec::new();
```

**File:** crates/apollo_l1_events/src/l1_scraper_tests.rs (L347-358)
```rust
// A convertible LogMessageToL2 event: calldata must lead with a from_address for msg-hash calc.
fn log_message_to_l2_event() -> L1Event {
    L1Event::LogMessageToL2 {
        tx: L1HandlerTransaction {
            calldata: Calldata(vec![Felt::ONE].into()),
            ..Default::default()
        },
        fee: Fee::default(),
        l1_tx_hash: None,
        block_timestamp: BlockTimestamp::default(),
    }
}
```

**File:** crates/apollo_node/src/servers.rs (L744-759)
```rust
pub async fn run_component_servers(servers: SequencerNodeServers) {
    // TODO(Tsabary): check if we can use create_servers instead of extending a new
    // FuturesUnordered.
    let mut all_servers = FuturesUnordered::new();
    all_servers.extend(servers.local_servers.run().await);
    all_servers.extend(servers.remote_servers.run().await);
    all_servers.extend(servers.wrapper_servers.run().await);

    if let Some(servers_type) = all_servers.next().await {
        // TODO(Tsabary): check all tasks are exited properly in case of a server failure before
        // panicking.
        panic!("{servers_type} Servers ended unexpectedly.");
    } else {
        unreachable!("all_servers is never empty");
    }
}
```
