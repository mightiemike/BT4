[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/coin.move (L905-917)
```text
    /// Deposit the coin balance into the recipient's account and emit an event.
    public fun deposit<CoinType>(
        account_addr: address, coin: Coin<CoinType>
    ) acquires CoinConversionMap, CoinInfo {
        primary_fungible_store::deposit(account_addr, coin_to_fungible_asset(coin));
    }

    public fun deposit_with_signer<CoinType>(
        account: &signer, coin: Coin<CoinType>
    ) acquires CoinConversionMap, CoinInfo {
        let account_address = signer::address_of(account);
        deposit(account_address, coin);
    }
```

**File:** storage/indexer/src/event_v2_translator.rs (L607-638)
```rust
struct TokenDepositTranslator;
impl EventV2Translator for TokenDepositTranslator {
    fn translate_event_v2_to_v1(
        &self,
        v2: &ContractEventV2,
        engine: &EventV2TranslationEngine,
    ) -> Result<ContractEventV1> {
        let deposit = TokenDeposit::try_from_bytes(v2.event_data())?;
        let struct_tag = StructTag::from_str("0x3::token::TokenStore")?;
        let (key, sequence_number) = if let Some(state_value_bytes) =
            engine.get_state_value_bytes_for_resource(deposit.account(), &struct_tag)?
        {
            let token_store_resource: TokenStoreResource = bcs::from_bytes(&state_value_bytes)?;
            let key = *token_store_resource.deposit_events().key();
            let sequence_number = engine
                .get_next_sequence_number(&key, token_store_resource.deposit_events().count())?;
            (key, sequence_number)
        } else {
            // If the token store resource is not found, we skip the event translation to avoid panic
            // because the creation number cannot be decided.
            return Err(AptosDbError::from(anyhow::format_err!(
                "Token store resource not found"
            )));
        };
        let deposit_event = TokenDepositEvent::new(deposit.id().clone(), deposit.amount());
        Ok(ContractEventV1::new(
            key,
            sequence_number,
            TOKEN_DEPOSIT_EVENT_TYPE.clone(),
            bcs::to_bytes(&deposit_event)?,
        )?)
    }
```

**File:** storage/aptosdb/src/schema/event/mod.rs (L49-57)
```rust
impl ValueCodec<EventSchema> for ContractEvent {
    fn encode_value(&self) -> Result<Vec<u8>> {
        bcs::to_bytes(self).map_err(Into::into)
    }

    fn decode_value(data: &[u8]) -> Result<Self> {
        bcs::from_bytes(data).map_err(Into::into)
    }
}
```
