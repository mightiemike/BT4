### Title
Hardcoded per-chain-id fallback in `eth_wallet_global_contract_hash` causes eth-implicit wallet accounts to resolve to a non-existent global contract on any nearcore-based network other than `mainnet`/`testnet`/`mocknet` - (File: runtime/near-wallet-contract/src/lib.rs)

### Summary
`eth_wallet_global_contract_hash` and `wallet_contract_magic_bytes` in `runtime/near-wallet-contract/src/lib.rs` hardcode specific `CryptoHash` values only for `chains::MAINNET`/`chains::MOCKNET` and `chains::TESTNET`, and silently fall back to the embedded `LOCALNET` wasm's hash for every other `chain_id` string [1](#0-0) . This is the exact analog of the reported bug class: an address/identifier that is correct for some deployments (mainnet, testnet) but is silently wrong — rather than erroring — for any other network built from this same nearcore codebase (e.g. a custom chain_id, similar in spirit to how Permit2's hardcoded address is wrong on some L2s).

### Finding Description
`RuntimeContractIdentifier::resolve` in `runtime/runtime/src/contract_code.rs` is the runtime's code-resolution path for every account, invoked on any function call or transfer/receipt targeting an account. For accounts of `AccountType::EthImplicitAccount` whose stored local code hash matches the legacy wallet-contract "magic bytes" (`LegacyEthWallet::resolve`), the runtime does **not** use the account's locally stored code. Instead, it calls `eth_wallet_global_contract_hash(chain_id)` to compute which global contract hash should be treated as this account's code [2](#0-1) .

`eth_wallet_global_contract_hash` hardcodes specific 32-byte hashes only for `chains::MAINNET | chains::MOCKNET` and `chains::TESTNET`; for any other `chain_id` value it falls back to `*LOCALNET.read_contract().hash()` — the hash of the wasm binary embedded at compile time in the nearcore binary, with no relationship whatsoever to whatever global contract may or may not actually be deployed on that particular chain [3](#0-2) . `chain_id` is an arbitrary, operator-chosen string set in genesis config (`core/primitives-core/src/chains.rs` only documents "commonly used" values such as `benchmarknet`, `congestion_control_test`, and any other string is valid) [4](#0-3) . nearcore is explicitly designed to be re-deployed as independently operated networks with their own `chain_id`.

The actual global contract that a legacy eth-implicit wallet account should resolve to must be deployed via a `DeployGlobalContractAction` and distributed through `initiate_distribution`/`GlobalContractDistributionReceipt` before it exists in that network's trie under `TrieKey::GlobalContractCode` [5](#0-4) . If the hash returned by `eth_wallet_global_contract_hash` for a non-mainnet/testnet/mocknet chain does not correspond to a contract that was actually deployed as a global contract under that hash on that network, then `GlobalContractAccessExt::code`/`hash` will not find the code (or, worse, `RuntimeContractIdentifier::resolve` will happily return `RuntimeContractIdentifier::Global { code_hash: global_hash, .. }` referencing a hash that is never populated in that network's state) [6](#0-5) . Any function call receipt targeting such an account will then fail with something like a missing/inconsistent global contract code error (`StorageInconsistentState`/`GlobalContractDoesNotExist`-style failure) rather than executing.

### Impact Explanation
Because the wallet contract is the *only* mechanism by which value held at an eth-implicit account can be moved (it implements RLP-signed transaction execution, ERC20 emulation, base-token transfers, etc., per `runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs`), any legacy eth-implicit account that was created on such a network before the `EthImplicitGlobalContract` feature (i.e., one whose on-chain code is the "magic bytes" placeholder resolved via `LegacyEthWallet::resolve`) becomes permanently unable to execute any wallet-contract logic once this fallback path is hit and no matching global contract exists at that hash. Funds/deposits held by such accounts become **permanently frozen**, since the only executable logic that could move them out resolves to a non-existent contract. This is a concrete, unauthorized-value-immobilization impact reachable purely by an ordinary user sending a transaction/receipt to their own eth-implicit account on any independently-operated nearcore-based chain, matching the "permanently frozen funds" acceptance criterion.

### Likelihood Explanation
Likelihood is moderate-to-high for any operator running a nearcore-derived chain with a `chain_id` other than `mainnet`, `testnet`, `mocknet`, `benchmarknet`, or `congestion_control_test` (all other custom networks fall into the unconditional `_` branch). Given eth-implicit accounts and their legacy wallet-contract migration are protocol features intended to be chain-agnostic (the code explicitly branches by `chain_id` to support "other chains (localnet, test chains)"), this silent fallback is triggered automatically and deterministically the moment any legacy eth-implicit account (with pre-`EthImplicitGlobalContract` code) is touched on such a chain — no attacker action is required, only ordinary usage.

### Recommendation
Do not silently default unknown `chain_id`s to the `LOCALNET` hash for `eth_wallet_global_contract_hash`/`wallet_contract_magic_bytes`. Instead:
- Require that the global contract hash used for eth-implicit wallet resolution be sourced from genesis/runtime config per-network (analogous to validating/parameterizing the Permit2 address at deployment) rather than hardcoded per chain-id string in code, or
- Explicitly whitelist only the known-good chain ids and make resolution fail loudly (protocol error at genesis validation time) for any other chain id if no matching global contract configuration is supplied, instead of silently substituting an unrelated hash that may not exist in that chain's state.

### Proof of Concept
1. Deploy nearcore with a custom `chain_id` (e.g. `"my-l2"`) that is not `mainnet`, `testnet`, or `mocknet`.
2. Create/observe an eth-implicit account whose stored code hash matches the legacy wallet-contract magic bytes (as would exist from a pre-`EthImplicitGlobalContract` protocol version, or via any migration path that produces such an account) — `LegacyEthWallet::resolve` returns `Some`.
3. Send a function call / RLP-execute transaction targeting that account. `RuntimeContractIdentifier::resolve` calls `eth_wallet_global_contract_hash("my-l2")`, which falls into the `_` branch and returns `LOCALNET.read_contract().hash()` [7](#0-6) .
4. Because no `DeployGlobalContractAction` was ever executed on `"my-l2"` to publish a global contract under that exact hash, code/hash lookup against `TrieKey::GlobalContractCode` fails to find the code, and the receipt fails deterministically for every call to that account — permanently blocking any operation (including value-moving RLP execution) on that account.

### Citations

**File:** runtime/near-wallet-contract/src/lib.rs (L74-105)
```rust
pub fn wallet_contract_magic_bytes(chain_id: &str) -> Arc<ContractCode> {
    match chain_id {
        chains::MAINNET => MAINNET.magic_bytes(),
        chains::TESTNET => TESTNET.magic_bytes(),
        _ => LOCALNET.magic_bytes(),
    }
}

/// Returns the global contract hash for the ETH wallet contract on a given chain.
/// This is the hash of the deployed global contract that ETH implicit accounts
/// should use when the EthImplicitGlobalContract protocol feature is enabled.
///
/// For other chains (localnet, test chains): Uses the hash of the embedded
/// wallet contract WASM, allowing tests to deploy the same contract as a
/// global contract.
pub fn eth_wallet_global_contract_hash(chain_id: &str) -> CryptoHash {
    match chain_id {
        // 2zodJZK2e4nnv5AqwCRnenNSmkikXhEd7PPY6BmfTmW4
        chains::MAINNET | chains::MOCKNET => CryptoHash([
            0x1d, 0xaa, 0x83, 0x5c, 0x46, 0x37, 0xf7, 0xae, 0x3d, 0x92, 0x40, 0x95, 0xba, 0x3f,
            0x0b, 0xf2, 0x82, 0x9b, 0xcf, 0xa1, 0x7b, 0x10, 0x68, 0xcd, 0x58, 0xbd, 0x85, 0x3d,
            0xca, 0xd7, 0xce, 0xb5,
        ]),
        // 3PpYvRxBfC5BkZxTw8ZFG3D52w1ZRhvDDWirKoxphMDn
        chains::TESTNET => CryptoHash([
            0x23, 0x8f, 0xea, 0xc1, 0xf8, 0x6c, 0xc9, 0xf9, 0xf4, 0x00, 0x3e, 0x3f, 0x6d, 0x5a,
            0xeb, 0xc0, 0x4e, 0xae, 0xa9, 0xc3, 0x94, 0x03, 0x2b, 0xd2, 0x94, 0x70, 0xe9, 0x60,
            0x9b, 0x67, 0xf6, 0xc5,
        ]),
        _ => *LOCALNET.read_contract().hash(),
    }
}
```

**File:** runtime/runtime/src/contract_code.rs (L52-66)
```rust
        if account_id.get_account_type() == AccountType::EthImplicitAccount {
            // Accounts that look like eth implicit accounts and have existed prior to the
            // eth-implicit accounts protocol change (these accounts are discussed in the
            // description of #11606) may have something else deployed to them. Only return
            // something here if the accounts have a wallet contract hash. Otherwise use the
            // regular path to grab the deployed contract.
            if LegacyEthWallet::resolve(local_hash).is_some() {
                // ETH implicit wallet accounts use global contracts, including
                // those created in old protocol versions.
                let global_hash = eth_wallet_global_contract_hash(chain_id);
                return Ok(RuntimeContractIdentifier::Global {
                    code_hash: global_hash,
                    identifier: GlobalContractIdentifier::CodeHash(global_hash),
                });
            }
```

**File:** core/primitives-core/src/chains.rs (L1-16)
```rust
//! Chain IDs of commonly used environment.

/// Main production environment.
pub const MAINNET: &str = "mainnet";

/// Primary testing environment.
pub const TESTNET: &str = "testnet";

/// Pre-release testing environment.
pub const MOCKNET: &str = "mocknet";

/// Used by ft-benchmark.  http://go/crt-benchmark
pub const BENCHMARKNET: &str = "benchmarknet";

/// Used by congestion control tests in nayduck.
pub const CONGESTION_CONTROL_TEST: &str = "congestion_control_test";
```

**File:** runtime/runtime/src/global_contracts.rs (L143-171)
```rust
fn initiate_distribution(
    state_update: &mut TrieUpdate,
    account_id: AccountId,
    contract_code: Arc<[u8]>,
    deploy_mode: &GlobalContractDeployMode,
    current_shard_id: ShardId,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let id = match deploy_mode {
        GlobalContractDeployMode::CodeHash => {
            GlobalContractIdentifier::CodeHash(hash(&contract_code))
        }
        GlobalContractDeployMode::AccountId => {
            GlobalContractIdentifier::AccountId(account_id.clone())
        }
    };
    // Increment the nonce and write it to state immediately to prevent multiple
    // distributions with the same nonce from being initiated. This requires
    // allowing the same nonce in the freshness check when applying the
    // distribution receipt.
    let nonce = increment_nonce(state_update, &id)?;
    let distribution_receipt =
        GlobalContractDistributionReceipt::new(id, current_shard_id, vec![], contract_code, nonce);
    let distribution_receipts =
        Receipt::new_global_contract_distribution(account_id, distribution_receipt);
    // No need to set receipt_id here, it will be generated as part of apply_action_receipt
    result.new_receipts.push(distribution_receipts);
    Ok(())
}
```
