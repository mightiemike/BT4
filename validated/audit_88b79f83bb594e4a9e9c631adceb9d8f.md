### Title
Hardcoded, unverifiable `eth_wallet_global_contract_hash` binds ETH-implicit accounts to a global contract that may not match the deployed code, permanently bricking all ETH-implicit accounts on that chain - ([File: runtime/near-wallet-contract/src/lib.rs])

### Summary
The runtime resolves the executable code for ETH-implicit accounts (accounts created via NEP-518 whose legacy local code hash matches a known wallet-contract magic value) by looking up a hardcoded `CryptoHash` constant per chain ID via `eth_wallet_global_contract_hash()`, then treating that hash as a `GlobalContractIdentifier::CodeHash` to fetch code from the global-contract trie storage. This is structurally the same class of bug as the reported issue: a hardcoded address/identifier constant that is supposed to reference "the correct deployed contract" but is baked into the binary with no on-chain verification and no way to correct it except a protocol upgrade.

### Finding Description
`eth_wallet_global_contract_hash` returns a compiled-in constant hash for `MAINNET`/`TESTNET` (and derives the localnet one from the embedded WASM) with no cross-check against what was actually deployed as a global contract on-chain: [1](#0-0) 

This hash is consumed directly in the contract-resolution path used on every function call/receipt execution against an ETH-implicit account: [2](#0-1) 

The resolved `RuntimeContractIdentifier::Global { code_hash: global_hash, .. }` is then used to fetch code by key `TrieKey::GlobalContractCode { identifier: GlobalContractIdentifier::CodeHash(global_hash) }` from trie storage: [3](#0-2) 

If the constant returned by `eth_wallet_global_contract_hash` for a given `chain_id` does not exactly match the hash of the WASM code that was actually deployed on-chain via `DeployGlobalContractAction` (analogous to the DAI oracle pointing at the wrong Uniswap pool address instead of the `StaticOracle`), every call into any ETH-implicit account on that chain resolves to a global-contract code hash for which no code exists in the trie. The lookup then hits the `ok_or_else` branch and returns `StorageError::StorageInconsistentState`, which is a fatal/unrecoverable runtime error path rather than a normal execution failure. There is no mechanism to update these hardcoded per-chain constants except shipping a new binary/protocol version, precisely mirroring the audit report's core complaint: "no way to set oracle addresses once deployed thus it cannot be changed."

The tests only self-check the constants against expectations baked into the same test file, they do not check them against any genuinely externally deployed global contract: [4](#0-3) 

### Impact Explanation
Any unprivileged party can trigger this: simply sending a transaction/receipt (`FunctionCall`, `Transfer` via `rlp_execute`, etc.) to any ETH-implicit account on a network where the constant is mismatched relative to the actually-deployed global contract causes that call to hit `StorageInconsistentState`, a code path documented elsewhere in the runtime as indicating corrupted state rather than user error. Because the constant is a compiled binary value shared network-wide, this is not a per-account misconfiguration but a chain-wide one: it would deterministically brick every ETH-implicit account's ability to execute any action (`rlp_execute`) network-wide until a protocol upgrade ships a corrected constant, effectively freezing funds/access held under all ETH-implicit accounts (Wallet Contract users) and halting their transaction processing. If honest nodes ever run binaries whose hardcoded hash differs (e.g. during a coordinated version rollout, or a chain such as `mocknet`/a fork that reused the mainnet branch but deployed different bytes), this would also manifest as a state-transition/consistency divergence between nodes expecting different global-contract hashes.

### Likelihood Explanation
This requires an actual mismatch between the hardcoded constant and the deployed global contract bytes for a given chain, which should not occur under correct release engineering for `mainnet`/`testnet` since the values are presumably produced from the actual deployed WASM. However, the pattern is inherently fragile: nothing in the runtime cross-validates the constant against on-chain deployed code, so any discrepancy (e.g., wrong WASM embedded during a rebuild, wrong hash pasted for a new chain id, or `localnet`/custom-chain configurations diverging from what a given node actually deployed) is trivially and permanently exploitable/triggerable by ordinary users just interacting with any ETH-implicit account, and is only fixable via a protocol version bump — matching the audit's "no way to set the address once deployed" finding.

### Recommendation
- Add build-time or genesis-time verification that ties `eth_wallet_global_contract_hash(chain_id)` to code that is actually present as a deployed global contract before it's trusted for resolution, or derive it dynamically from the currently deployed contract rather than a hardcoded literal.
- Add a runtime-level assertion at startup (not just a unit test) that fails fast/loudly if the configured global-contract hash for the running chain does not correspond to retrievable global-contract code, rather than allowing it to silently surface as `StorageInconsistentState` mid-execution.
- Provide a protocol-gated mechanism (already partially present via `GlobalContractIdentifier::AccountId`) to remap the ETH wallet contract reference without requiring a hardcoded, unauditable hash per chain.

### Proof of Concept
1. On a test/local chain, deploy a global contract by code hash whose bytes differ from what `LOCALNET`/the chain-specific constant in `eth_wallet_global_contract_hash` expects (simulating a mismatched constant, analogous to `StableOracleDAI`'s misconfigured `DAIEthOracle` pointing at the wrong contract type/address).
2. Create an ETH-implicit account (legacy wallet-contract-style local code hash) and send an `rlp_execute`/`FunctionCall` action to it.
3. Observe that `RuntimeContractIdentifier::resolve` in `runtime/runtime/src/contract_code.rs` returns `Global { code_hash: <hardcoded_hash> }`, and the subsequent `GlobalContractAccessExt::code`/`hash` lookup in `runtime/runtime/src/contract_code.rs` fails to find code under that hash in the trie, surfacing `StorageError::StorageInconsistentState` for every call against that (and all other) ETH-implicit accounts on the chain — a total, un-patchable-without-upgrade denial of the Wallet Contract feature network-wide.

### Citations

**File:** runtime/near-wallet-contract/src/lib.rs (L82-105)
```rust
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

**File:** runtime/near-wallet-contract/src/lib.rs (L193-202)
```rust
    #[test]
    fn test_eth_wallet_global_contract_hash_values() {
        let mainnet_expected: CryptoHash =
            "2zodJZK2e4nnv5AqwCRnenNSmkikXhEd7PPY6BmfTmW4".parse().unwrap();
        let testnet_expected: CryptoHash =
            "3PpYvRxBfC5BkZxTw8ZFG3D52w1ZRhvDDWirKoxphMDn".parse().unwrap();
        assert_eq!(eth_wallet_global_contract_hash(MAINNET), mainnet_expected);
        assert_eq!(eth_wallet_global_contract_hash(MOCKNET), mainnet_expected);
        assert_eq!(eth_wallet_global_contract_hash(TESTNET), testnet_expected);
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

**File:** runtime/runtime/src/contract_code.rs (L108-116)
```rust
    fn code(self, store: &TrieUpdate) -> Result<Option<ContractCode>, StorageError> {
        let key = TrieKey::GlobalContractCode { identifier: self.clone().into() };
        let code_hash = match self {
            GlobalContractIdentifier::AccountId(_) => None,
            GlobalContractIdentifier::CodeHash(hash) => Some(hash),
        };
        let code = store.get(&key, AccessOptions::DEFAULT)?;
        Ok(code.map(|code| ContractCode::new(code, code_hash)))
    }
```
