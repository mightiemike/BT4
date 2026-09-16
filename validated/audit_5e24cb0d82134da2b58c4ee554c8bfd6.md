### Title
Unsafe zero-confirmation `finality` default for L1 event/gas-price scrapers with no enforced minimum - ([File: crates/apollo_l1_events_config/src/config.rs])

### Summary
Both `L1EventsScraperConfig` and `L1GasPriceScraperConfig` define `finality: u64` with **no minimum validation** and a **default value of `0`**, meaning the sequencer treats the tip of L1 as final with zero confirmations by default. This is the same bug class as the reported issue (chain confirmations defaulting to an unsafe minimum with no enforced floor), except here the shipped default is even weaker (0 instead of 1).

### Finding Description
`L1EventsScraperConfig::finality` and `L1GasPriceScraperConfig::finality` are both plain `u64` fields with no `#[validate(range(min = ...))]` attribute, unlike other fields in the same structs (e.g. `max_blocks_per_fetch` explicitly has `#[validate(range(min = 1))]`): [1](#0-0) 

Their `Default` impls set `finality: 0`: [2](#0-1) [3](#0-2) 

This zero default is also what ships in the generated node config schema, confirming it is the effective production default absent an explicit override: [4](#0-3) [5](#0-4) 

`finality` is used to compute the "safe" L1 tip the scraper will read from — `latest_l1_block_number - finality`: [6](#0-5) [7](#0-6) 

With `finality = 0`, this reduces to using the raw latest L1 block with no reorg buffer at all, and the events window ceiling collapses to the unconfirmed tip: [8](#0-7) 

The codebase's own reorg-handling tests demonstrate the risk directly: with `finality = 1` ("low_finality") a short reorg is *not* safely absorbed and results in an `L1ReorgDetected` error being surfaced only after the fact — i.e., the scraper had already acted on data from a block that got reverted: [9](#0-8) 

With `finality = 0` (the shipped default), the scraper has even less margin than the "low_finality" case tested, so the L1 handler transaction ingestion path (`L1EventsScraperConfig`) and the L1 gas price feed (`L1GasPriceScraperConfig`) are both operating with the weakest possible reorg protection by default, with nothing in config validation to prevent it.

### Impact Explanation
`L1EventsScraperConfig.finality` gates when an L1→L2 message (an `L1Handler` transaction) becomes visible/validated to the L1 events provider (see the finality-gated flow test): [10](#0-9) 

If an L1 message is scraped from a block that later reorgs out (trivial on an unfinalized tip with `finality=0`), the sequencer can validate/propose an `L1Handler` transaction corresponding to a message that never actually finalized on L1 (e.g., a bridge deposit). This can lead to an unauthorized L1-triggered account action being admitted into the L2 chain, or divergence between nodes that observed the L1 chain at different points around the reorg — both of which map to "unauthorized account action" / "honest-node divergence" outcomes. The gas price scraper sharing the same unsafe default similarly risks basing L2 fee/resource accounting on gas prices computed from blocks subject to reorg, which can cause committed-block fee data to diverge from what a re-execution against the finalized L1 view would produce.

### Likelihood Explanation
This requires no attacker privilege beyond the ability to induce or benefit from an ordinary L1 reorg (even a 1-2 block reorg, common on L1 during normal operation) around the time an L1→L2 message is sent — an unprivileged L1 message sender scenario explicitly in scope. Because `finality = 0` is the actual default baked into the config schema and no validator enforces a safe minimum, any deployment that does not explicitly override this value in its environment-specific config inherits the unsafe behavior.

### Recommendation
Add `#[validate(range(min = N))]` (matching the existing pattern used for `max_blocks_per_fetch`) to the `finality` fields of `L1EventsScraperConfig` and `L1GasPriceScraperConfig`, and change their `Default` impls to a safe, chain-appropriate non-zero confirmation count (e.g., the value already used in the deployment presets, if any exist) rather than `0`.

### Proof of Concept
1. Deploy a node using the default `L1EventsScraperConfig` (or any config that does not explicitly override `finality`), which resolves to `finality = 0` as shown in `crates/apollo_node/resources/config_schema.json:3277-3281`.
2. Send an L1→L2 message in an L1 block `B`.
3. Before block `B` is confirmed by even a single subsequent block, trigger/observe a natural reorg that replaces `B`.
4. Because `finality = 0`, `fetch_events`/`fetch_start_block` in `crates/apollo_l1_events/src/l1_scraper.rs` treats the L1 tip (including `B`) as already final and scrapes/exposes the message immediately, before any reorg-safety margin — as demonstrated by the analogous `l1_short_reorg_gas_price_scraper_is_fine` test showing `finality <= 1` fails to absorb even a short reorg (`crates/apollo_l1_gas_price/src/l1_gas_price_scraper_test.rs:209-282`).
5. The sequencer may validate/propose an `L1Handler` transaction for a message that is later invalidated on L1, or diverge from peers that observed the reorg at a different time.

### Citations

**File:** crates/apollo_l1_events_config/src/config.rs (L106-123)
```rust
#[derive(Clone, Debug, Serialize, Deserialize, Validate, PartialEq)]
pub struct L1EventsScraperConfig {
    #[serde(deserialize_with = "deserialize_float_seconds_to_duration")]
    pub startup_rewind_time_seconds: Duration,
    #[validate(custom(function = "validate_ascii"))]
    pub chain_id: ChainId,
    pub finality: u64,
    #[serde(deserialize_with = "deserialize_float_seconds_to_duration")]
    pub polling_interval_seconds: Duration,
    pub set_provider_historic_height_to_l2_genesis: bool,
    #[serde(deserialize_with = "deserialize_float_seconds_to_duration")]
    pub l1_block_time_seconds: Duration,
    /// Maximum number of L1 blocks fetched per `events` (eth_getLogs) request. Caps the catch-up
    /// window so a large backlog is drained over successive polls instead of one unbounded
    /// request.
    #[validate(range(min = 1))]
    pub max_blocks_per_fetch: u64,
}
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

**File:** crates/apollo_l1_gas_price_config/src/config.rs (L542-552)
```rust
impl Default for L1GasPriceScraperConfig {
    fn default() -> Self {
        Self {
            starting_block: None,
            chain_id: ChainId::Other("0x0".to_string()),
            finality: 0,
            polling_interval: Duration::from_secs(1),
            number_of_blocks_for_mean: 300,
            startup_num_blocks_multiplier: 2,
        }
    }
```

**File:** crates/apollo_node/resources/config_schema.json (L3277-3281)
```json
  "l1_events_scraper_config.finality": {
    "description": "Number of blocks to wait for finality",
    "privacy": "Public",
    "value": 0
  },
```

**File:** crates/apollo_node/resources/config_schema.json (L3452-3456)
```json
  "l1_gas_price_scraper_config.finality": {
    "description": "Number of blocks to wait for finality in L1",
    "privacy": "Public",
    "value": 0
  },
```

**File:** crates/apollo_l1_events/src/l1_scraper.rs (L132-144)
```rust
        let finality = self.config.finality;
        let latest_l1_block_number = self
            .base_layer
            .latest_l1_block_number()
            .await
            .map_err(L1EventsScraperError::BaseLayerError)?;
        let latest_l1_block_number = latest_l1_block_number.checked_sub(finality).ok_or(
            L1EventsScraperError::LatestBlockNumberTooLow {
                latest_l1_block_no_finality: latest_l1_block_number,
                finality,
            },
        )?;
        debug!("Latest L1 block number: {latest_l1_block_number:?}");
```

**File:** crates/apollo_l1_events/src/l1_scraper.rs (L271-290)
```rust
        let latest_l1_block_number = self
            .base_layer
            .latest_l1_block_number()
            .await
            .map_err(L1EventsScraperError::BaseLayerError)?;
        let latest_l1_block_number = latest_l1_block_number
            .checked_sub(self.config.finality)
            .ok_or(L1EventsScraperError::LatestBlockNumberTooLow {
                finality: self.config.finality,
                latest_l1_block_no_finality: latest_l1_block_number,
            })?;
        let latest_l1_block = self
            .base_layer
            .l1_block_at(latest_l1_block_number)
            .await
            .map_err(L1EventsScraperError::BaseLayerError)?
            .ok_or(L1EventsScraperError::LatestL1BlockNumberNoBlockFound {
                block_number: latest_l1_block_number,
            })?;

```

**File:** crates/apollo_l1_events/src/l1_scraper_tests.rs (L283-314)
```rust
// The window ceiling is finality-adjusted: it must never request beyond latest - finality.
#[tokio::test]
async fn fetch_events_window_respects_finality_ceiling() {
    const MAX_BLOCKS_PER_FETCH: u64 = 1000;
    const FINALITY: u64 = 6;
    const START_BLOCK_NUMBER: u64 = 50;
    const LATEST_BLOCK_NUMBER: u64 = 60;
    const L1_BLOCK_HASH: L1BlockHash = L1BlockHash([7; 32]);
    // The backlog (10 blocks) is smaller than the window, so the ceiling is latest - finality.
    const EXPECTED_WINDOW_END: u64 = LATEST_BLOCK_NUMBER - FINALITY;

    let mut base_layer = MockBaseLayerContract::new();
    base_layer.expect_latest_l1_block_number().returning(|| Ok(LATEST_BLOCK_NUMBER));
    base_layer
        .expect_l1_block_at()
        .returning(move |number| Ok(Some(L1BlockReference { number, hash: L1_BLOCK_HASH })));
    base_layer
        .expect_events()
        .withf(|block_range, _| *block_range.end() == EXPECTED_WINDOW_END)
        .times(1)
        .returning(|_, _| Ok(vec![]));

    let mut scraper = scraper_with_dummy().await;
    scraper.config.max_blocks_per_fetch = MAX_BLOCKS_PER_FETCH;
    scraper.config.finality = FINALITY;
    scraper.scrape_from_this_l1_block =
        Some(L1BlockReference { number: START_BLOCK_NUMBER, hash: L1_BLOCK_HASH });
    scraper.base_layer = base_layer;

    let (window_end_block, _events) = scraper.fetch_events().await.unwrap();
    assert_eq!(window_end_block.number, EXPECTED_WINDOW_END);
}
```

**File:** crates/apollo_l1_gas_price/src/l1_gas_price_scraper_test.rs (L209-282)
```rust
#[rstest]
#[case::high_finality(3)]
#[case::low_finality(1)]
#[tokio::test]
async fn l1_short_reorg_gas_price_scraper_is_fine(#[case] finality: u64) {
    const START_BLOCK: u64 = 0;
    const END_BLOCK: u64 = 10;
    const REORG_BLOCK: u64 = 9;

    let end_of_chain = Arc::new(AtomicU64::new(END_BLOCK));
    let end_of_chain_clone = end_of_chain.clone();
    let has_reorg_happened = Arc::new(AtomicBool::new(false));
    let has_reorg_happened_clone = has_reorg_happened.clone();

    // Returns a spoof hash, based on the block number and whether a reorg happened.
    fn block_hash_calculator(block_number: u64, is_reorg: bool) -> L1BlockHash {
        let mut hash_number = block_number;
        if is_reorg && block_number >= REORG_BLOCK {
            // If a reorg happened, we change the hash number, but only for blocks after
            // REORG_BLOCK.
            hash_number += 100;
        }
        u64_to_block_hash(hash_number)
    }

    // Explicitly making the mocks here, so we can customize them for the test.
    let mut mock_contract = MockBaseLayerContract::new();
    // This expectation just returns the last block number.
    mock_contract
        .expect_latest_l1_block_number()
        .returning(move || Ok(end_of_chain_clone.load(Ordering::SeqCst)));
    // This expectation will return the regular chain, or the chain with the reorg (depending on
    // has_reorg_happened).
    mock_contract.expect_get_block_header().returning(move |block_number| {
        // We never return None, since latest_l1_block_number will stop earlier, due to finality.
        let reorg = has_reorg_happened_clone.load(Ordering::SeqCst);

        let mut header = create_l1_block_header(block_number);
        header.hash = block_hash_calculator(block_number, reorg);
        header.parent_hash = block_hash_calculator(block_number.saturating_sub(1), reorg);
        Ok(Some(header))
    });
    let mut mock_provider = MockL1GasPriceProviderClient::new();
    mock_provider.expect_add_price_info().withf(check_gas_prices).returning(|_| Ok(()));

    // Make a scraper with the finality set.
    let mut scraper = L1GasPriceScraper::new(
        L1GasPriceScraperConfig { finality, ..Default::default() },
        Arc::new(mock_provider),
        mock_contract,
    );
    // The first call should succeed.
    let mut block_number = START_BLOCK;
    scraper.update_prices(&mut block_number).await.unwrap();
    // Successfully scraped the first blocks (we don't reach END_BLOCK, because of finality).
    assert_eq!(block_number, END_BLOCK - finality + 1);

    // Now we simulate a reorg by setting has_reorg_happened to true.
    has_reorg_happened.store(true, Ordering::SeqCst);
    // We allow the chain to keep going to a higher block number.
    end_of_chain.store(END_BLOCK + finality * 2, Ordering::SeqCst);
    // The second call should succeed, as the scraper will handle the reorg.
    let result = scraper.update_prices(&mut block_number).await;

    if finality > 1 {
        // High finality case, means we can safely skip over this short reorg.
        result.unwrap();
        // The final block number should be one after the end of the chain minus finality.
        assert_eq!(block_number, end_of_chain.load(Ordering::SeqCst) - finality + 1);
    } else {
        // Low finality case, means we will trigger a reorg error.
        assert!(matches!(result, Err(L1GasPriceScraperError::L1ReorgDetected { .. })));
    }
}
```

**File:** crates/apollo_l1_events/tests/flow_test_finality.rs (L25-81)
```rust
#[tokio::test]
async fn only_scrape_after_finality() {
    // Setup.
    const FINALITY: u64 = 3;

    // Setup the base layer.
    let mut base_layer = setup_anvil_base_layer().await;

    let (l2_hash, _nonce) = send_message_from_l1_to_l2(&mut base_layer, CALL_DATA).await;

    let l1_events_scraper_config = L1EventsScraperConfig {
        finality: FINALITY,
        polling_interval_seconds: POLLING_INTERVAL_DURATION,
        chain_id: CHAIN_ID,
        ..Default::default()
    };
    let l1_events_provider_client = setup_scraper_and_provider(
        base_layer.ethereum_base_layer.clone(),
        Some(l1_events_scraper_config),
    )
    .await;

    tokio::time::pause();

    // Test.
    let next_block_height = BlockNumber(TARGET_L2_HEIGHT.0 + 1);

    // The transaction is not yet scraped, it hasn't got enough blocks after it.
    l1_events_provider_client.start_block(SessionState::Validate, next_block_height).await.unwrap();
    assert_eq!(
        l1_events_provider_client.validate(l2_hash, next_block_height).await.unwrap(),
        ValidationStatus::Invalid(InvalidValidationStatus::NotFound)
    );

    // Send few more blocks (by sending more txs).
    for _ in 0..FINALITY {
        let (other_l2_hash, _nonce) =
            send_message_from_l1_to_l2(&mut base_layer, CALL_DATA_2).await;
        assert_ne!(l2_hash, other_l2_hash);
    }

    // Wait for another scraping.
    tokio::time::advance(POLLING_INTERVAL_DURATION + ROUND_TO_SEC_MARGIN_DURATION).await;
    for _i in 0..100 {
        let snapshot = l1_events_provider_client.get_l1_events_provider_snapshot().await.unwrap();
        if snapshot.uncommitted_transactions.contains(&l2_hash) {
            break;
        }
        tokio::time::sleep(WAIT_FOR_ASYNC_PROCESSING_DURATION).await;
    }

    // Check that we can validate the message now.
    assert_eq!(
        l1_events_provider_client.validate(l2_hash, next_block_height).await.unwrap(),
        ValidationStatus::Validated
    );
}
```
