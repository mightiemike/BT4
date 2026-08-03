Confirmed: `Account::balance` in `api/src/accounts.rs` combines the coin-store balance and the fungible-store balance using plain unchecked `u64` addition, with no `checked_add`/`saturating_add`/`u128` widening anywhere in that file. [1](#0-0) [2](#0-1) 

### Title
Unchecked `u64` addition when combining `CoinStore` and paired `FungibleStore` balances can overflow/wrap in `Account::balance` - (File: api/src/accounts.rs)

### Summary
`Account::balance` computes the externally-reported balance for a coin type as `coin_balance` (from `CoinStoreResourceUntyped::coin()`) plus `fa_store_resource.balance()` (and, alternatively, `ConcurrentFungibleBalanceResource::balance()`), all as plain `u64` values combined with `+=` and no overflow checking: [3](#0-2) [4](#0-3) 

The Move-side equivalent, `coin::balance`, has the identical unchecked-addition pattern (`coin_balance<CoinType>(owner) + primary_fungible_store::balance(...)`), so the same defect exists at the source-of-truth view function, not only in the API layer: [5](#0-4) 

### Finding Description
Both fields being summed are independently `u64`-bounded (`CoinStoreResourceUntyped.coin: u64`, `FungibleStoreResource.balance: u64`), but the *total supply* of a given `CoinType` is tracked as an `OptionalAggregator`/aggregator value, which is `u128`-based, not `u64`-bounded. This means it is architecturally possible for the sum of a single account's coin-store balance and paired-fungible-store balance for the same underlying asset to exceed `u64::MAX`, if that account holds a very large amount split between the legacy `CoinStore` representation and the newer `FungibleStore` representation. The `Account::balance` handler adds these two `u64` values together into a `u64` accumulator without any bounds checking, widening, or use of `checked_add`. In a release build this silently wraps (produces an incorrect, smaller value); in a debug build it panics.

### Impact Explanation
If reached, this produces an incorrect API response value — the returned `balance` field would not equal the true combined value of the account's committed on-chain state (`CoinStoreResource.coin() + FungibleStoreResource.balance()`), violating the required invariant that authenticated API responses must equal the sum of underlying committed state values without overflow. This is a single-account balance-read correctness bug (silent wrap in release builds, or a panic/service disruption in debug builds), not a state-corruption or proof-binding issue — no committed ledger state, write set, or proof is altered.

### Likelihood Explanation
Reaching this condition on mainnet requires a single account to simultaneously hold values in *both* the legacy `CoinStore` and the paired `FungibleStore` for the same `CoinType` whose sum approaches `u64::MAX` (i.e., roughly 18.4 quintillion base units). For `AptosCoin` this is economically infeasible given the actual circulating supply. For an arbitrary user-published `CoinType` (permissionless coin publishing is possible), the coin creator does control mint capability and could mint large amounts into their own `CoinStore`, but:
- `migrate_to_fungible_store` moves the *entire* `CoinStore` balance to the `FungibleStore` atomically (all-or-nothing), so ordinary migration does not leave both stores independently near-max simultaneously.
- I was not able to fully verify, within the available tooling/index, an unprivileged/permissionless code path that lets a coin creator independently deposit near-`u64::MAX` amounts directly into *both* the `CoinStore` and the paired primary `FungibleStore` for the same `CoinType` without going through the atomic migration function (this would require access to the paired `MintRef`/`PairedFungibleAssetRefs` outside the framework-internal migration flow). This is a gap in my verification, not a confirmed impossibility.

Given this uncertainty, the finding is a real, unguarded arithmetic defect in the API/view code, but its practical exploitability by a fully unprivileged actor on mainnet against a state that matters (e.g. an economically significant coin) is not established by what I could verify — it depends on being able to independently inflate both balance fields for the same coin pairing to near-`u64::MAX`, which the framework's atomic migration design appears to resist.

### Recommendation
Regardless of current reachability, harden `Account::balance` (and the analogous `coin::balance` Move function) to use checked/saturating arithmetic, e.g. widen to `u128` for the addition and either saturate at `u64::MAX` or return an explicit error/128-bit value instead of silently wrapping or panicking, so the invariant "API response equals sum of committed state without overflow" holds unconditionally rather than relying on economic limits on total supply.

### Proof of Concept
Not constructible with certainty from the available code/index alone: doing so would require confirming a permissionless path to simultaneously set both `CoinStoreResource.coin()` and the paired `FungibleStoreResource.balance()` for the *same* `CoinType`/account to values whose `u64` sum overflows, which was not found within the reviewed files (`migrate_to_fungible_store` is atomic/all-or-nothing, and direct dual-deposit access to the paired `MintRef` was not located). A background engineering session with full repo/tooling access would be needed to confirm or rule out such a path before treating this as exploitable rather than a defensive-coding gap.

### Citations

**File:** api/src/accounts.rs (L319-366)
```rust
    pub fn balance(
        &self,
        asset_type: AssetType,
        accept_type: &AcceptType,
    ) -> BasicResultWith404<u64> {
        let (fa_metadata_address, mut balance) = match asset_type {
            AssetType::Coin(move_struct_tag) => {
                let coin_store_type_tag =
                    StructTag::from_str(&format!("0x1::coin::CoinStore<{}>", move_struct_tag))
                        .map_err(|err| {
                            BasicErrorWith404::internal_with_code(
                                err,
                                AptosErrorCode::InternalError,
                                &self.latest_ledger_info,
                            )
                        })?;
                // query coin balance
                let state_value = self.context.get_state_value_poem(
                    &StateKey::resource(&self.address.into(), &coin_store_type_tag).map_err(
                        |err| {
                            BasicErrorWith404::internal_with_code(
                                err,
                                AptosErrorCode::InternalError,
                                &self.latest_ledger_info,
                            )
                        },
                    )?,
                    self.ledger_version,
                    &self.latest_ledger_info,
                )?;
                let coin_balance = match state_value {
                    None => 0,
                    Some(bytes) => bcs::from_bytes::<CoinStoreResourceUntyped>(&bytes)
                        .map_err(|err| {
                            BasicErrorWith404::internal_with_code(
                                err,
                                AptosErrorCode::InternalError,
                                &self.latest_ledger_info,
                            )
                        })?
                        .coin(),
                };
                (
                    get_paired_fa_metadata_address(&move_struct_tag),
                    coin_balance,
                )
            },
            AssetType::FungibleAsset(fa_metadata_adddress) => (fa_metadata_adddress.into(), 0),
```

**File:** api/src/accounts.rs (L389-411)
```rust
                    if fa_store_resource.balance != 0 {
                        balance += fa_store_resource.balance();
                    } else if let Some(concurrent_fa_balance) = object_group
                        .group
                        .get(&ConcurrentFungibleBalanceResource::struct_tag())
                    {
                        // query potential concurrent fa balance
                        let concurrent_fa_balance_resource =
                            bcs::from_bytes::<ConcurrentFungibleBalanceResource>(
                                concurrent_fa_balance,
                            )
                            .map_err(|err| {
                                BasicErrorWith404::internal_with_code(
                                    err,
                                    AptosErrorCode::InternalError,
                                    &self.latest_ledger_info,
                                )
                            })?;
                        balance += concurrent_fa_balance_resource.balance();
                    }
                }
            }
        }
```

**File:** aptos-move/framework/aptos-framework/sources/coin.move (L750-760)
```text
    #[view]
    /// Returns the balance of `owner` for provided `CoinType` and its paired FA if exists.
    public fun balance<CoinType>(owner: address): u64 acquires CoinConversionMap, CoinStore {
        let paired_metadata = paired_metadata<CoinType>();
        coin_balance<CoinType>(owner)
            + if (paired_metadata.is_some()) {
                primary_fungible_store::balance(
                    owner, paired_metadata.extract()
                )
            } else { 0 }
    }
```
