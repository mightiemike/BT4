This is a valid analog. The hardcoded `_INIT_CODE_HASH` bug class — a fixed, unchecked cryptographic constant baked into code that other logic silently trusts to reference the correct deployed bytecode/address — maps directly onto `eth_wallet_global_contract_hash` in nearcore, which hardcodes per-chain `CryptoHash` constants used to point every newly created ETH-implicit account at a "global contract" by hash, with no runtime verification that the hash actually corresponds to a deployed, usable Wallet Contract.

### Title
Hardcoded, Unverified `eth_wallet_global_contract_hash` Constants Can Permanently Freeze Funds Sent to ETH-Implicit Accounts - (File: `runtime/near-wallet-contract/src/lib.rs`)

### Summary
`eth_wallet_global_contract_hash(chain_id)` returns hardcoded `CryptoHash` byte arrays for `MAINNET`/`TESTNET` that are used, unconditionally and without any existence/consistency check, to set `AccountContract::Global(hash)` on every newly created ETH-implicit account.

### Finding Description
When the `EthImplicitGlobalContract` protocol feature is enabled, any implicit-account-creation `Transfer` to a `0x...` address routes through `action_implicit_account_creation_transfer` in `runtime/runtime/src/actions.rs`. For the `EthImplicitAccount` branch, the code calls: [1](#0-0) 

`global_contract_hash` comes straight from the hardcoded per-chain table: [2](#0-1) 

This hash is written into the account record (`AccountContract::Global(global_contract_hash)`) with no lookup or existence check against the actual global contract stored in the trie at creation time — the check only happens later, lazily, when code is fetched via `GlobalContractAccessExt::code`/`hash`: [3](#0-2) 

If the constant is wrong for a given chain (mismatched, stale after a wallet-contract redeploy, or simply a typo/copy-paste error analogous to the reported `_INIT_CODE_HASH`), the mismatch is silent at account-creation time: the `Transfer` succeeds and creates the account pointing at a global-contract hash for which no code is actually deployed on that shard.

### Impact Explanation
Once created this way, an ETH-implicit account can only ever be operated through the Wallet Contract's `rlp_execute` method — per spec, it "cannot be deleted, nor can a full access key be added": [4](#0-3) 

If the hardcoded hash does not resolve to an actually-deployed global contract, every `FunctionCall` against that account fails (`GlobalContractDoesNotExist`), permanently — since no other action type is permitted on an ETH-implicit account. Any NEAR sent via the initiating `Transfer`, and any subsequent transfer into that same address, becomes permanently unreachable: there is no full-access key, no delete path, and no working contract entry point. This is systemic, since a single wrong constant affects every ETH-implicit account ever created on that chain, not just one deployment — an unprivileged attacker or even ordinary users triggering the bug merely by sending a plain `Transfer` to a `0x...`-format receiver id.

### Likelihood Explanation
Triggering the path requires nothing more than a single `Transfer` transaction to an address-shaped (`0x` + 40 hex) account id — something any unprivileged transaction signer can do, and in fact something wallets/relayers are expected to do routinely to onboard ETH-compatible users (this is exactly the mechanism NEP-518 Wallet Contract accounts are designed to receive). No validator collusion, no special permissions, and no network-layer conditions are required; correctness rests entirely on the hardcoded constant matching whatever global contract was actually deployed and referenced by governance/genesis tooling for that `chain_id`.

### Recommendation
Do not trust a hardcoded hash as ground truth for account creation. At minimum, assert in a startup/genesis-config validation step (or via a protocol-level invariant check) that `eth_wallet_global_contract_hash(chain_id)` actually resolves to an existing global contract before the `EthImplicitGlobalContract` feature is allowed to activate, and add a regression test that hashes the currently-deployed Wallet Contract WASM and compares it against the hardcoded constants for `MAINNET`/`TESTNET`/`MOCKNET`, failing CI on mismatch.

### Proof of Concept
1. Suppose `eth_wallet_global_contract_hash(chains::MAINNET)` is (or becomes, e.g. after a wallet-contract upgrade whose hash update is forgotten) inconsistent with the actual global contract stored under `TrieKey::GlobalContractCode` on-chain.
2. Any account sends a `Transfer` action to a syntactically valid `0x...` receiver id that does not yet exist.
3. `action_implicit_account_creation_transfer` (`runtime/runtime/src/actions.rs:255-269`) creates the account with `AccountContract::Global(<hardcoded-but-wrong-hash>)`, taking the deposit.
4. Any later `rlp_execute` `FunctionCall` on that account fails to resolve code via `GlobalContractAccessExt` (`runtime/runtime/src/contract_code.rs:91-116`), returning `GlobalContractDoesNotExist`.
5. Because ETH-implicit accounts cannot receive a `FullAccess` key or be deleted, the deposited funds are permanently unreachable.

### Citations

**File:** runtime/runtime/src/actions.rs (L255-269)
```rust
        AccountType::EthImplicitAccount => {
            let chain_id = epoch_info_provider.chain_id();

            // Use a deployed global contract for ETH implicit accounts.
            let global_contract_hash = eth_wallet_global_contract_hash(&chain_id);
            let storage_usage = fee_config.storage_usage_config.num_bytes_account
                + global_contract_hash.as_bytes().len() as u64;

            *account = Some(Account::new(
                deposit,
                Balance::ZERO,
                AccountContract::Global(global_contract_hash),
                storage_usage,
            ));
        }
```

**File:** runtime/near-wallet-contract/src/lib.rs (L89-105)
```rust
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

**File:** runtime/runtime/src/contract_code.rs (L91-116)
```rust
impl GlobalContractAccessExt for GlobalContractIdentifier {
    fn hash(self, store: &TrieUpdate, access: AccessOptions) -> Result<CryptoHash, StorageError> {
        if let GlobalContractIdentifier::CodeHash(hash) = self {
            return Ok(hash);
        }
        let key = TrieKey::GlobalContractCode { identifier: self.into() };
        let value_ref =
            store.get_ref(&key, KeyLookupMode::MemOrFlatOrTrie, access)?.ok_or_else(|| {
                let TrieKey::GlobalContractCode { identifier } = key else { unreachable!() };
                StorageError::StorageInconsistentState(format!(
                    "Global contract identifier not found {:?}",
                    identifier
                ))
            })?;
        Ok(value_ref.value_hash())
    }

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

**File:** docs/DataStructures/Account.md (L119-123)
```markdown
Once a NEAR-implicit account is created it acts as a regular account until it's deleted.

An ETH-implicit account can only be used by calling the methods of the [Wallet Contract](#wallet-contract). It cannot be deleted, nor can a full access key be added.
The primary purpose of ETH-implicit accounts is to enable seamless integration of existing Ethereum tools (such as wallets) with the NEAR blockchain.

```
