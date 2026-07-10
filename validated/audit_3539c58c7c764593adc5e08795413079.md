### Title
Missing migration path for the current state layout makes any future field addition permanently brick the contract — (`contract/src/lib.rs`)

### Summary

`BtcLightClient` is an upgradeable NEAR contract. Its `migrate()` function only recognises two Borsh layouts: the current one and the single historical layout `BtcLightClientV2`. There is no reserved space (no `__gap` equivalent) and no migration path from the *current* layout to any future layout. If a developer adds a new field to `BtcLightClient` during the next upgrade without simultaneously adding a migration branch for the current layout, `migrate()` will panic on every call, permanently bricking the contract and making all Bitcoin-transaction verification on NEAR impossible.

### Finding Description

`BtcLightClient` is decorated with `#[derive(Upgradable)]` from `near_plugins`, giving DAO/CodeDeployer roles the ability to stage and deploy new WASM code on-chain. [1](#0-0) 

The sole migration entry-point is `migrate()`, which reads the raw Borsh state from storage and tries two parse attempts in order: [2](#0-1) 

```
1. try_from_slice::<BtcLightClient>(&raw_state)   // no-op if already current
2. try_from_slice::<BtcLightClientV2>(&raw_state)  // drops used_aux_parent_blocks
3. panic("contract state matches no known layout")
```

`BtcLightClientV2` is the only historical snapshot kept: [3](#0-2) 

The current `BtcLightClient` struct has eight user-defined fields and no padding or reserved slots: [4](#0-3) 

Borsh serialisation is strict: `try_from_slice` fails if the byte buffer is not *exactly* consumed. Adding even one field to `BtcLightClient` changes the expected byte length. After such an upgrade:

- Branch 1 fails — the on-chain state (old layout) is shorter than the new struct expects.
- Branch 2 fails — the on-chain state is the *current* layout, not `BtcLightClientV2`.
- Branch 3 executes: `env::panic_str("contract state matches no known layout")`.

Because `migrate()` is `#[init(ignore_state)]`, every subsequent call to it also panics. Normal contract calls (which deserialise state automatically) also panic with "Cannot deserialize the contract state." The contract is permanently frozen.

This is the direct NEAR/Borsh analog of the missing Solidity `__gap` variable: there is no reserved space to absorb future fields, and the migration function has no path from the current layout to any successor layout.

### Impact Explanation

After a field-adding upgrade without a matching migration branch:

- `verify_transaction_inclusion` and `verify_transaction_inclusion_v2` — the primary public API consumed by recipient contracts — become permanently unreachable.
- `submit_blocks` is also unreachable, so the canonical chain tip is frozen.
- `migrate()` itself panics, so there is no recovery path short of redeploying a contract that can parse the corrupted state externally and reconstruct it.

All downstream NEAR contracts that rely on SPV proofs from this light client lose their ability to verify Bitcoin transactions.

### Likelihood Explanation

The contract has already undergone one state migration (V2 → current, dropping `used_aux_parent_blocks`), demonstrating that field additions are a normal part of the development lifecycle. The `Upgradable` plugin and the DAO/CodeDeployer role structure are explicitly designed to support future upgrades. The probability that a future upgrade adds a new field is high; the probability that a developer forgets to add the corresponding migration branch for the *current* layout (which is not yet listed as a historical snapshot) is also non-trivial, because the current code provides no structural reminder (no `__gap`, no versioned snapshot of the current layout).

### Recommendation

- **Short term:** Add a snapshot of the current layout as `BtcLightClientV3` (or equivalent) inside the `migrate` module immediately, and add a branch in `migrate()` that handles `BtcLightClientV3 → BtcLightClient` (the next version). This mirrors the pattern already used for `BtcLightClientV2` and ensures the current layout is always a recognised historical state.
- **Long term:** Adopt a versioned state pattern: store an explicit version tag as the first Borsh field, or maintain a changelog of all historical struct snapshots inside the `migrate` module so that every past layout is always a recognised migration source.

### Proof of Concept

1. Deploy the current contract and initialise it (state is now in the current `BtcLightClient` layout).
2. Add one new field (e.g., `last_gc_height: u64`) to `BtcLightClient` in a new version, without adding a migration branch for the current layout.
3. Deploy the new WASM via `up_deploy_code`.
4. Call `migrate()` as the contract account.
   - Branch 1: `try_from_slice::<BtcLightClientNewVersion>` fails — the stored bytes are 8 bytes shorter than expected.
   - Branch 2: `try_from_slice::<BtcLightClientV2>` fails — the stored bytes match the current layout, not V2.
   - Result: `env::panic_str("contract state matches no known layout")`.
5. Call `verify_transaction_inclusion_v2` from any account — panics with "Cannot deserialize the contract state."
6. The contract is permanently bricked with no on-chain recovery path.

### Citations

**File:** contract/src/lib.rs (L85-95)
```rust
#[access_control(role_type(Role))]
#[near(contract_state)]
#[derive(Pausable, Upgradable, PanicOnDefault)]
#[pausable(manager_roles(Role::PauseManager))]
#[upgradable(access_control_roles(
    code_stagers(Role::CodeStager, Role::DAO),
    code_deployers(Role::CodeDeployer, Role::DAO),
    duration_initializers(Role::DurationManager, Role::DAO),
    duration_update_stagers(Role::DurationManager, Role::DAO),
    duration_update_appliers(Role::DurationManager, Role::DAO),
))]
```

**File:** contract/src/lib.rs (L96-118)
```rust
pub struct BtcLightClient {
    // A pair of lookup maps that allows to find header by height and height by header
    mainchain_height_to_header: LookupMap<u64, H256>,
    mainchain_header_to_height: LookupMap<H256, u64>,

    // Block with the highest chainWork, i.e., blockchain tip, you can find latest height inside of it
    mainchain_tip_blockhash: H256,

    // The oldest block in main chain we store
    mainchain_initial_blockhash: H256,

    // Mapping of block hashes to block headers (ALL ever submitted, i.e., incl. forks)
    headers_pool: LookupMap<H256, ExtendedHeader>,

    // If we should run all the block checks or not
    skip_pow_verification: bool,

    // GC threshold - how many blocks we would like to store in memory, and GC the older ones
    gc_threshold: u64,

    // Network type Mainnet/Testnet
    network: Network,
}
```

**File:** contract/src/lib.rs (L693-704)
```rust
    #[derive(BorshDeserialize, BorshSerialize, PanicOnDefault)]
    pub struct BtcLightClientV2 {
        mainchain_height_to_header: LookupMap<u64, H256>,
        mainchain_header_to_height: LookupMap<H256, u64>,
        mainchain_tip_blockhash: H256,
        mainchain_initial_blockhash: H256,
        headers_pool: LookupMap<H256, ExtendedHeader>,
        skip_pow_verification: bool,
        gc_threshold: u64,
        used_aux_parent_blocks: near_sdk::collections::LookupSet<H256>,
        network: Network,
    }
```

**File:** contract/src/lib.rs (L726-750)
```rust
        pub fn migrate() -> Self {
            let raw_state = env::storage_read(b"STATE")
                .unwrap_or_else(|| env::panic_str("contract state not found"));

            if let Ok(state) = <Self as BorshDeserialize>::try_from_slice(&raw_state) {
                log!("state is already in the current layout");
                return state;
            }

            if let Ok(old_state) = BtcLightClientV2::try_from_slice(&raw_state) {
                log!("migrating state from the V2 layout");
                return Self {
                    mainchain_height_to_header: old_state.mainchain_height_to_header,
                    mainchain_header_to_height: old_state.mainchain_header_to_height,
                    mainchain_tip_blockhash: old_state.mainchain_tip_blockhash,
                    mainchain_initial_blockhash: old_state.mainchain_initial_blockhash,
                    headers_pool: old_state.headers_pool,
                    skip_pow_verification: old_state.skip_pow_verification,
                    gc_threshold: old_state.gc_threshold,
                    network: old_state.network,
                };
            }

            env::panic_str("contract state matches no known layout")
        }
```
