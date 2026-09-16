## Title
Unbounded L1 Message Payload Size Permanently Stalls the L1 Events Scraper via Non-Bisecting Retry - (File: crates/apollo_l1_events/src/l1_scraper.rs)

### Summary
The `L1EventsScraper` (`crates/apollo_l1_events/src/l1_scraper.rs`) is the sequencer's analog of the Gravity Bridge's Ethereum Oracle: it polls L1 for `LogMessageToL2` and related events via `eth_getLogs` and feeds them to the L1 provider so L1→L2 messages become proposable L1-handler transactions. Like the audited bug, the scraper bounds its query only by *block count* (`max_blocks_per_fetch`), not by the *byte size* of the RPC response, and on an `eth_getLogs` failure it retries the identical `[start, end]` window forever rather than bisecting it or skipping ahead.

### Finding Description
`fetch_events` in [1](#0-0) . The underlying `events()` implementation issues a single `get_logs` call over that whole range with no chunking by response size: [2](#0-1) .

The L1 `sendMessageToL2` entry point accepts an arbitrary-length `uint256[] payload` [3](#0-2) , and this payload is echoed back verbatim in the `LogMessageToL2` event [4](#0-3) . Any unprivileged L1 account can therefore inflate the log data returned for a given block range essentially without bound (bounded only by L1 gas limits per call, but an attacker can issue many such calls across the blocks inside one scrape window).

When the resulting `eth_getLogs` response exceeds a size limit enforced by the RPC provider (a very common real-world limit, exactly as described in the referenced Gravity Bridge report), `events()` returns an error, which propagates as `L1EventsScraperError::BaseLayerError`. Critically, the scraper's retry behavior is proven not to bisect or otherwise shrink the window on failure: [5](#0-4) . Similarly, [6](#0-5) . The main loop simply logs a warning and retries on the same interval forever: [7](#0-6) .

Consequently, if an attacker permanently bloats the log data for a fixed block window beyond the RPC provider's response-size limit (by sending enough large-payload `sendMessageToL2` calls into blocks within that window before it is scraped), the scraper can never successfully fetch past that window — since it always re-requests the exact same range on failure with no fallback strategy (bisection, chunking, or byte-size-aware retry). `scrape_from_this_l1_block` never advances, `L1_MESSAGE_SCRAPER_BASELAYER_ERROR_COUNT` increments forever, and no messages sent after the malicious block ever reach the sequencer.

### Impact Explanation
This freezes the entire L1→L2 messaging pipeline of the sequencer: no new L1 handler transactions can ever be included in blocks once the scraper is stuck, because the `L1EventsProvider`/transaction manager (fed exclusively via `add_events`/`initialize` from this scraper) never receives events past the poisoned block range. This is a permanent network freeze of a core Starknet capability (L1 message bridging) triggerable by a single unprivileged L1 message sender, matching the accepted impact criteria (network unable to confirm/include new transactions of a class). It requires operator intervention (e.g., raising `max_blocks_per_fetch` won't help — a smaller window only needs a smaller amount of spam to exceed the byte limit; the fix must be response-size-aware) to unstick — exactly analogous to the original Gravity Bridge finding requiring a patch to re-enable the bridge.

### Likelihood Explanation
Likelihood is high: `sendMessageToL2` is a completely permissionless L1 function requiring only L1 gas payment, no validator signatures, and no protocol-level cap on payload array length is enforced on L1 or checked by the scraper before issuing `eth_getLogs`. An attacker only needs to spend gas proportional to the desired log bloat (cheaper with sparse/zero payload data, as noted in the original report) within a handful of blocks matching one scrape window (default `max_blocks_per_fetch = 1000` blocks [8](#0-7) ) to exceed common public-RPC/provider response size caps.

### Recommendation
- Make `fetch_events`/`events()` size-aware: on an `eth_getLogs` (or generic RPC) failure that looks like a response-size/payload-too-large error, bisect the block range and retry with progressively smaller windows (as recommended in the original report) instead of blindly re-issuing the identical range forever.
- Alternatively/additionally, enforce and validate a maximum total payload size per scraped window (e.g., a running byte budget across blocks) so the scraper proactively shrinks `window_end_number` before hitting the RPC limit.
- Consider capping `sendMessageToL2` payload length at the L1 contract level (if under this project's control) so a single call cannot contribute unbounded bytes to a single block's logs.
- Emit a distinguishable error/metric for "response too large" vs. other `BaseLayerError`s so operators can be alerted specifically to this failure mode.

### Proof of Concept
1. An attacker (any L1 account) repeatedly calls the Starknet core contract's `sendMessageToL2(toAddress, selector, payload)` with a very large `payload` array (e.g., tens of thousands of `uint256` elements) across a handful of consecutive L1 blocks, paying only L1 gas.
2. Once the sequencer's `scrape_from_this_l1_block` cursor reaches a window (`[scraping_start_number, window_end_number]`, capped by `max_blocks_per_fetch`, see [1](#0-0) ) that includes these malicious blocks, `self.base_layer.events(...)` issues `eth_getLogs` for that range and receives a payload exceeding the connected RPC provider's response-size limit, per [2](#0-1) .
3. `fetch_events` returns `L1EventsScraperError::BaseLayerError`; per the proven retry semantics in [5](#0-4) , the scraper retries the exact same window on the next `polling_interval_seconds` tick, hitting the same oversized response and failing again — indefinitely.
4. `scrape_from_this_l1_block` never advances past the malicious window (confirmed by [6](#0-5) ), so no `LogMessageToL2`/`ConsumedMessageToL2`/cancellation events after that point are ever delivered to the `L1EventsProvider`, permanently halting inclusion of new L1-handler transactions.

### Citations

**File:** crates/apollo_l1_events/src/l1_scraper.rs (L100-119)
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
```

**File:** crates/apollo_l1_events/src/l1_scraper.rs (L304-330)
```rust
        let scraping_start_number = scrape_from_this_l1_block.number + 1;
        // Cap the fetched range to max_blocks_per_fetch so a large backlog is drained over
        // successive polls rather than in one unbounded eth_getLogs request. The window is
        // inclusive on both ends, hence the `- 1`; `.max(1)` and saturating arithmetic guard
        // against underflow even though config validation already rejects 0. The finality ceiling
        // (latest_l1_block.number) is preserved.
        let window_end_number = latest_l1_block
            .number
            .min(scraping_start_number.saturating_add(self.config.max_blocks_per_fetch.max(1) - 1));

        // Reuse latest_l1_block when the window already reaches it, to avoid a needless extra RPC.
        let window_end_block = if window_end_number == latest_l1_block.number {
            latest_l1_block
        } else {
            self.base_layer
                .l1_block_at(window_end_number)
                .await
                .map_err(L1EventsScraperError::BaseLayerError)?
                .ok_or(L1EventsScraperError::LatestL1BlockNumberNoBlockFound {
                    block_number: window_end_number,
                })?
        };

        let scraping_result = self
            .base_layer
            .events(scraping_start_number..=window_end_number, &self.tracked_event_identifiers)
            .await;
```

**File:** crates/papyrus_base_layer/src/ethereum_base_layer_contract.rs (L166-184)
```rust
    #[instrument(skip(self), err)]
    async fn events<'a>(
        &'a mut self,
        block_range: RangeInclusive<u64>,
        event_types_to_filter: &'a [&'a str],
    ) -> EthereumBaseLayerResult<Vec<L1Event>> {
        // Don't actually need mutability here, and using mut self doesn't work with async move in
        // the loop below.
        let immutable_self = &*self;
        let filter = EthEventFilter::new()
            .select(block_range.clone())
            .events(event_types_to_filter)
            .address(immutable_self.config.starknet_contract_address);

        let matching_logs = tokio::time::timeout(
            immutable_self.config.timeout_millis,
            immutable_self.contract.provider().get_logs(&filter),
        )
        .await??;
```

**File:** crates/papyrus_base_layer/resources/Starknet-0.10.3.4.json (L97-139)
```json
        {
            "anonymous": false,
            "inputs": [
                {
                    "indexed": true,
                    "internalType": "address",
                    "name": "fromAddress",
                    "type": "address"
                },
                {
                    "indexed": true,
                    "internalType": "uint256",
                    "name": "toAddress",
                    "type": "uint256"
                },
                {
                    "indexed": true,
                    "internalType": "uint256",
                    "name": "selector",
                    "type": "uint256"
                },
                {
                    "indexed": false,
                    "internalType": "uint256[]",
                    "name": "payload",
                    "type": "uint256[]"
                },
                {
                    "indexed": false,
                    "internalType": "uint256",
                    "name": "nonce",
                    "type": "uint256"
                },
                {
                    "indexed": false,
                    "internalType": "uint256",
                    "name": "fee",
                    "type": "uint256"
                }
            ],
            "name": "LogMessageToL2",
            "type": "event"
        },
```

**File:** crates/papyrus_base_layer/resources/Starknet-0.10.3.4.json (L588-620)
```json
        {
            "inputs": [
                {
                    "internalType": "uint256",
                    "name": "toAddress",
                    "type": "uint256"
                },
                {
                    "internalType": "uint256",
                    "name": "selector",
                    "type": "uint256"
                },
                {
                    "internalType": "uint256[]",
                    "name": "payload",
                    "type": "uint256[]"
                }
            ],
            "name": "sendMessageToL2",
            "outputs": [
                {
                    "internalType": "bytes32",
                    "name": "",
                    "type": "bytes32"
                },
                {
                    "internalType": "uint256",
                    "name": "",
                    "type": "uint256"
                }
            ],
            "stateMutability": "payable",
            "type": "function"
```

**File:** crates/apollo_l1_events/src/l1_scraper_tests.rs (L360-386)
```rust
// A getLogs failure must not advance the cursor; the same window is retried on the next poll.
#[tokio::test]
async fn cursor_not_advanced_on_events_rpc_failure() {
    const START_BLOCK_NUMBER: u64 = 42;
    const MAX_BLOCKS_PER_FETCH: u64 = 1000;
    const LATEST_BLOCK_NUMBER: u64 = START_BLOCK_NUMBER + 5;
    const L1_BLOCK_HASH: L1BlockHash = L1BlockHash([7; 32]);

    let mut base_layer = MockBaseLayerContract::new();
    base_layer.expect_latest_l1_block_number().returning(|| Ok(LATEST_BLOCK_NUMBER));
    base_layer
        .expect_l1_block_at()
        .returning(move |number| Ok(Some(L1BlockReference { number, hash: L1_BLOCK_HASH })));
    base_layer.expect_events().returning(|_, _| Err(MockError::MockError));

    let mut scraper = scraper_with_dummy().await;
    scraper.config.max_blocks_per_fetch = MAX_BLOCKS_PER_FETCH;
    scraper.scrape_from_this_l1_block =
        Some(L1BlockReference { number: START_BLOCK_NUMBER, hash: L1_BLOCK_HASH });
    scraper.base_layer = base_layer;

    assert_matches!(
        scraper.send_events_to_l1_events_provider().await,
        Err(L1EventsScraperError::BaseLayerError(_))
    );
    assert_eq!(scraper.scrape_from_this_l1_block.unwrap().number, START_BLOCK_NUMBER);
}
```

**File:** crates/apollo_l1_events/src/l1_scraper_tests.rs (L421-468)
```rust
// After a getLogs failure the retry must re-request the exact same [start, end] window (no bisect).
#[tokio::test]
async fn retry_refetches_same_window() {
    const START_BLOCK_NUMBER: u64 = 10;
    const MAX_BLOCKS_PER_FETCH: u64 = 100;
    const LATEST_BLOCK_NUMBER: u64 = 1000;
    const L1_BLOCK_HASH: L1BlockHash = L1BlockHash([7; 32]);
    // Inclusive window [start+1 ..= start+max], so the expected end is start + max.
    const EXPECTED_WINDOW_END: u64 = START_BLOCK_NUMBER + MAX_BLOCKS_PER_FETCH;

    let mut base_layer = MockBaseLayerContract::new();
    base_layer.expect_latest_l1_block_number().returning(|| Ok(LATEST_BLOCK_NUMBER));
    base_layer
        .expect_l1_block_at()
        .returning(move |number| Ok(Some(L1BlockReference { number, hash: L1_BLOCK_HASH })));
    // First attempt fails while requesting the capped window.
    base_layer
        .expect_events()
        .withf(|block_range, _| {
            *block_range.start() == START_BLOCK_NUMBER + 1
                && *block_range.end() == EXPECTED_WINDOW_END
        })
        .times(1)
        .returning(|_, _| Err(MockError::MockError));
    // The retry must request the identical window, not a bisected one.
    base_layer
        .expect_events()
        .withf(|block_range, _| {
            *block_range.start() == START_BLOCK_NUMBER + 1
                && *block_range.end() == EXPECTED_WINDOW_END
        })
        .times(1)
        .returning(|_, _| Ok(vec![]));

    let mut scraper = scraper_with_dummy().await;
    scraper.config.max_blocks_per_fetch = MAX_BLOCKS_PER_FETCH;
    scraper.scrape_from_this_l1_block =
        Some(L1BlockReference { number: START_BLOCK_NUMBER, hash: L1_BLOCK_HASH });
    scraper.base_layer = base_layer;

    assert_matches!(
        scraper.send_events_to_l1_events_provider().await,
        Err(L1EventsScraperError::BaseLayerError(_))
    );
    assert_eq!(scraper.scrape_from_this_l1_block.unwrap().number, START_BLOCK_NUMBER);

    scraper.send_events_to_l1_events_provider().await.unwrap();
    assert_eq!(scraper.scrape_from_this_l1_block.unwrap().number, EXPECTED_WINDOW_END);
```

**File:** crates/apollo_l1_events_config/src/config.rs (L125-139)
```rust
impl Default for L1EventsScraperConfig {
    fn default() -> Self {
        Self {
            startup_rewind_time_seconds: Duration::from_secs(60 * 60),
            chain_id: ChainId::Mainnet,
            finality: 0,
            polling_interval_seconds: Duration::from_secs(30),
            set_provider_historic_height_to_l2_genesis: false,
            l1_block_time_seconds: Duration::from_secs(12),
            // Conservative default: well under the common public-RPC eth_getLogs block-range caps
            // (~1k-10k) and the 1s base-layer timeout. Operators on permissive private RPCs may
            // raise it.
            max_blocks_per_fetch: 1000,
        }
    }
```
