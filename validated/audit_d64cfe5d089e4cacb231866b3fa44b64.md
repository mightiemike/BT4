### Title
Event-V2→V1 translation reads latest chain state instead of the version at which the event was emitted, corrupting historical event `EventKey`/`sequence_number` - (File: `storage/indexer/src/event_v2_translator.rs`)

### Summary
`MintTokenTranslator::translate_event_v2_to_v1` (and every other `EventV2Translator` impl in this file) derives the translated V1 event's `EventKey` and `sequence_number` by reading the `0x3::token::Collections` resource via `EventV2TranslationEngine::get_state_value_bytes_for_resource`, which unconditionally calls `self.main_db_reader.latest_state_checkpoint_view()` rather than a state view pinned to the ledger version of the `ContractEventV2` being translated.

### Finding Description
`MintTokenTranslator` looks up the creator's `Collections` resource to obtain the `mint_token_events` GUID and event count: [1](#0-0) 

That lookup goes through: [2](#0-1) 

`get_state_value_bytes_for_resource` calls `self.main_db_reader.latest_state_checkpoint_view()` — the state as of the *latest committed checkpoint* of the main DB — with no version parameter tying it to the version of `v2`. The same pattern is used by every other translator in the file (`TokenMutationTranslator`, `CollectionMutationTranslator`, `MintTranslator`, `BurnTranslator`, `TokenDepositTranslator`, `TokenDataCreationTranslator`, `CancelOfferTranslator`, etc.), all confirmed to call the identical `engine.get_state_value_bytes_for_resource` / `get_state_value_bytes_for_object_group_resource` helpers.

The `DBIndexer` tails behind the main DB (`storage/indexer/CLAUDE.md:32`: "`DBIndexer::process(start, end)` tail-follows the main DB"), meaning by the time it processes and translates the event committed at version N, the main DB may already have committed many more versions (up to whatever `latest_state_checkpoint_view` currently points to). Any subsequent transaction that mutates `Collections` (e.g., another mint, a token burn changing the event counter, or migration to `ConcurrentSupply`) before the indexer/backfill/replay job reaches and translates the event at version N will cause `translate_event_v2_to_v1` to compute the derived `EventKey`/`sequence_number` from the *wrong*, later state — not the state as it existed immediately after version N's execution.

### Impact Explanation
The translated `ContractEventV1` (with its `EventKey`/`sequence_number`) is persisted into `TranslatedV1EventSchema` and mirrored into the event-by-key/version indices, and it is what legacy/V1 event APIs (e.g. `/transactions/by_version/{version}`) return for that historical transaction version. If the resource state has since changed (e.g., the event handle's `counter` field has advanced further due to later mints, or the same address later switches to `ConcurrentSupply` and no longer has the `FixedSupply`/`UnlimitedSupply`/`Collections` resource at all), the derived key/sequence number stamped onto the *historically committed* event no longer matches what would have been computed at the correct version. This permanently corrupts the queryable/authenticated V1 event history returned by API and indexer read paths for that transaction, since translation results are cached/persisted once computed and are not re-derivable to the "correct" version-scoped truth after the fact.

### Likelihood Explanation
This is not a hypothetical operator error — it is a structural property of every translator in this file reading `latest_state_checkpoint_view()`. Any unprivileged mint/burn/transfer activity that continues to mutate the `Collections`/`FixedSupply`/`UnlimitedSupply`/`TokenStore`/`PendingClaims` resources for a given account/collection after an earlier V2 event was emitted — combined with the indexer's inherent tail-lag or any backfill/replay run that processes versions non-instantaneously — will trigger this divergence in normal operation, with no privileged access required.

### Recommendation
`EventV2TranslationEngine`'s resource-lookup helpers (`get_state_value_bytes_for_resource`, `get_state_value_bytes_for_object_group_resource`) should accept and use the exact ledger `Version` of the `ContractEventV2` being translated (e.g., via `main_db_reader.state_view_at_version(Some(version))` or equivalent) instead of `latest_state_checkpoint_view()`, so the derived `EventKey`/`sequence_number` is always bound to the resource state as of the event's own commit version.

### Proof of Concept
1. Submit a transaction at version N that emits a `0x3::token::Mint`-style `MintToken` V2 event for `creator` — at this point `Collections.mint_token_events.counter` = C.
2. Before the indexer's `DBIndexer::process`/translation step reaches version N (e.g., due to tail-lag, backfill, or another concurrent transaction being sequenced), submit another transaction at version N+1 from the same or another unprivileged account that mutates `Collections` for `creator` (e.g., another mint, or a switch of supply type), advancing the event counter/GUID state.
3. When translation for the version-N event finally runs, `MintTokenTranslator::translate_event_v2_to_v1` calls `engine.get_state_value_bytes_for_resource` → `main_db_reader.latest_state_checkpoint_view()`, which reads state at version N+1 (or later), not version N.
4. Assert: the persisted `TranslatedV1EventSchema` entry for the version-N event contains a `sequence_number`/`EventKey` derived from the version-N+1 counter, not the counter that existed immediately after version N — demonstrating that the derived key/sequence for a historical event is version-inconsistent with its actual commit point. [1](#0-0) [2](#0-1)

### Citations

**File:** storage/indexer/src/event_v2_translator.rs (L202-214)
```rust
    pub fn get_state_value_bytes_for_resource(
        &self,
        address: &AccountAddress,
        struct_tag: &StructTag,
    ) -> Result<Option<Bytes>> {
        let state_view = self
            .main_db_reader
            .latest_state_checkpoint_view()
            .expect("Failed to get state view");
        let state_key = StateKey::resource(address, struct_tag)?;
        let maybe_state_value = state_view.get_state_value(&state_key)?;
        Ok(maybe_state_value.map(|state_value| state_value.bytes().clone()))
    }
```

**File:** storage/indexer/src/event_v2_translator.rs (L752-775)
```rust
struct MintTokenTranslator;
impl EventV2Translator for MintTokenTranslator {
    fn translate_event_v2_to_v1(
        &self,
        v2: &ContractEventV2,
        engine: &EventV2TranslationEngine,
    ) -> Result<ContractEventV1> {
        let mint = MintToken::try_from_bytes(v2.event_data())?;
        let struct_tag = StructTag::from_str("0x3::token::Collections")?;
        let (key, sequence_number) = if let Some(state_value_bytes) =
            engine.get_state_value_bytes_for_resource(mint.creator(), &struct_tag)?
        {
            let token_store_resource: CollectionsResource = bcs::from_bytes(&state_value_bytes)?;
            let key = *token_store_resource.mint_token_events().key();
            let sequence_number = engine
                .get_next_sequence_number(&key, token_store_resource.mint_token_events().count())?;
            (key, sequence_number)
        } else {
            // If the collections store resource is not found, we skip the event translation to
            // avoid panic because the creation number cannot be decided.
            return Err(AptosDbError::from(anyhow::format_err!(
                "Collections resource not found"
            )));
        };
```
