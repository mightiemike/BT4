[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/coin.move (L670-681)
```text
    fun maybe_convert_to_fungible_store<CoinType>(
        account: address
    ) acquires CoinStore, CoinConversionMap, CoinInfo {
        if (exists<CoinStore<CoinType>>(account)) {
            let CoinStore<CoinType> { coin, frozen, deposit_events, withdraw_events } =
                move_from<CoinStore<CoinType>>(account);
            if (is_coin_initialized<CoinType>() && coin.value > 0) {
                let metadata = ensure_paired_metadata<CoinType>();
                let store =
                    primary_fungible_store::ensure_primary_store_exists(
                        account, metadata
                    );
```
