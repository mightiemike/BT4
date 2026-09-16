### Title
Permanent Freeze of L1→L2 Message Pipeline via Malformed L1 Event Causing `events()` to Fail Non-Recoverably - (File: `crates/papyrus_base_layer/src/ethereum_base_layer_contract.rs`)

### Summary
The Ethereum base layer's `events()` implementation aborts the *entire* batch of scraped L1 logs if any single log fails to parse into an `L1Event`/`EventData` — except for one specific, allow-listed error (`CalldataValueOutOfRange`), which is the only variant that is skipped. Any other decode error (e.g. `FeeOutOfRange`, missing block header/number, or any future parsing error added to `parse_event`) causes `events()` to return `Err`, which the scraper (`l1_scraper.rs`) treats as a `BaseLayerError` that does **not** advance `scrape_from_this_l1_block`. Since the offending L1 log is immutable once mined, the scraper will retry the exact same block window forever, permanently blocking all L1→L2 message delivery — the same failure mode as the Gravity Bridge H-02 bug (a single malformed/edge-case log freezes oracle progress forever).

### Finding Description
`EthereumBaseLayerContract::events()` parses each log independently via `parse_event(log, header.timestamp)`, and then filters results: [1](#0-0) 

Only `EthereumBaseLayerError::CalldataValueOutOfRange` is treated as a soft/skippable per-event error; every other error variant returned from `parse_event` (including `FeeOutOfRange`, decode errors, or missing block metadata) causes the whole function to `return Err(error)`, discarding the parse results for *all* other logs in the fetched range along with it.

`parse_event` in `eth_events.rs` converts `LogMessageToL2` fee via `event.fee.try_into().map_err(EthereumBaseLayerError::FeeOutOfRange)` and builds `EventData`/`L1HandlerTransaction` from raw event fields — this is directly attacker-influenced data from an L1 message sent to the Starknet core contract (`sendMessageToL2`), since the fee and payload values are supplied by the caller of the L1 contract.

Upstream, the scraper's `fetch_events()` calls `self.base_layer.events(...)` and maps any error to `L1EventsScraperError::BaseLayerError`: [2](#0-1) 

`send_events_to_l1_events_provider()` only advances the cursor (`self.scrape_from_this_l1_block = Some(latest_l1_block)`) on success; on `BaseLayerError` it warns and retries the *same* window on the next poll iteration, as explicitly verified by the test `cursor_not_advanced_on_events_rpc_failure`: [3](#0-2) 

Because the log range and its content never change (L1 logs are immutable once finalized), if a single log in that window always fails to parse for a non-skipped reason, the scraper is stuck retrying that same window on every polling interval indefinitely — it can never advance past the bad block, and consequently never picks up any subsequent legitimate `LogMessageToL2` events either, since scraping always starts from `scrape_from_this_l1_block + 1`.

### Impact Explanation
This causes a permanent Denial-of-Service on the L1→L2 messaging bridge: no new `L1HandlerTransaction`s can ever be scraped and delivered to L2 once a single unparseable log is emitted at or below the finality window and is included in a fetch batch. This is a "network unable to confirm new transactions" condition specifically for the L1 handler transaction pipeline — permanently freezing L1-originated funds/messages (e.g., deposits, cross-layer calls) with no self-healing path, matching the Critical impact class described in the report (bridge freeze until intervention).

### Likelihood Explanation
The trigger is reachable by any unprivileged L1 account calling `sendMessageToL2` on the Starknet core contract with adversarially chosen `fee`/payload values designed to trip a non-`CalldataValueOutOfRange` error path in `parse_event` (e.g. a fee value that overflows the `Fee` type via `FeeOutOfRange`, given `event.fee: U256` and `Fee` likely backed by a narrower integer). This requires no privileged access, no validator collusion, and costs only ordinary L1 gas plus the message fee — an "extremely low cost way to bring down the network," exactly as in the original Gravity Bridge finding.

### Recommendation
Do not fail the entire `events()` batch on a single log's parse error. Instead:
- Skip/log the specific malformed event (similar to the existing `CalldataValueOutOfRange` handling) rather than aborting the whole call, for all recoverable per-log errors (e.g. `FeeOutOfRange`).
- Alternatively, ensure the scraper cursor still advances past a block window even when specific events in it are unparseable, so the pipeline is not permanently wedged by one bad log.
- Add fuzz/property tests feeding out-of-range `fee`/payload values through `parse_event` and `events()` to confirm the scraper always makes forward progress.

### Proof of Concept
Conceptual PoC (cannot be executed without live L1/Anvil access in this analysis):
1. From an unprivileged L1 account, call `sendMessageToL2(to_address, selector, payload)` on the Starknet core contract with a `fee` value (or other field feeding `EventData::try_from`) chosen so that `event.fee.try_into()` in `parse_event` fails, producing `EthereumBaseLayerError::FeeOutOfRange` (not `CalldataValueOutOfRange`).
2. Once this transaction is included and reaches finality, the scraper's `fetch_events()` → `base_layer.events()` call will include this log in its window and hit the `Err(error) => return Err(error)` branch in `ethereum_base_layer_contract.rs`.
3. `send_events_to_l1_events_provider()` receives `L1EventsScraperError::BaseLayerError`, logs a warning, and does not advance `scrape_from_this_l1_block`.
4. Every subsequent polling interval, the scraper requests the identical `[scraping_start_number, window_end_number]` range containing the same bad log and fails identically — confirmed by the existing test pattern `cursor_not_advanced_on_events_rpc_failure` / `retry_refetches_same_window`, which shows the scraper deterministically re-fetches the same window on failure without any bisection or skip logic.
5. All legitimate `LogMessageToL2` events at or after this point are never scraped, permanently freezing the L1→L2 bridge.

Note: I was not able to fully confirm the exact concrete type/width mismatch that would make `event.fee.try_into()` fail in practice (i.e., whether `Fee`'s inner type can actually be exceeded by a `U256` fee value sent through the real Starknet core contract, since the contract may itself constrain `fee`) — this would need to be verified against the deployed L1 contract's ABI constraints and the `Fee` type definition in `starknet_api` to confirm exploitability of this specific error variant versus other potential non-skipped error paths in `parse_event`.

### Citations

**File:** crates/papyrus_base_layer/src/ethereum_base_layer_contract.rs (L204-216)
```rust
        // TODO(guyn): replace this with try_join_all.
        let events = futures::future::join_all(block_header_futures).await;
        let mut parsed_events = Vec::with_capacity(events.len());
        for event in events {
            match event {
                Ok(event) => parsed_events.push(event),
                Err(EthereumBaseLayerError::CalldataValueOutOfRange(_)) => {
                    warn!("Skipping event due to calldata value out of range {:?}", event);
                }
                Err(error) => return Err(error),
            }
        }
        Ok(parsed_events)
```

**File:** crates/apollo_l1_events/src/l1_scraper.rs (L327-332)
```rust
        let scraping_result = self
            .base_layer
            .events(scraping_start_number..=window_end_number, &self.tracked_event_identifiers)
            .await;

        let l1_events = scraping_result.map_err(L1EventsScraperError::BaseLayerError)?;
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
