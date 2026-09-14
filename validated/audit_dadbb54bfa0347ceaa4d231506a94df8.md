### Title
Unchecked global-contract-existence when creating ETH-implicit accounts permanently freezes deposited funds - (File: `runtime/runtime/src/actions.rs`)

### Summary
`action_implicit_account_creation_transfer` binds every newly-created ETH-implicit account unconditionally to `AccountContract::Global(eth_wallet_global_contract_hash(chain_id))` without ever checking that a global contract with that hash actually exists on the current shard's trie. Every other code path in the runtime that binds an account to a `GlobalContractIdentifier` (explicit `UseGlobalContractAction`, and `DeterministicStateInitAction`) performs that existence check and fails cleanly with `GlobalContractDoesNotExist` if the code hasn't propagated yet. The implicit ETH-account creation path is the sole exception, mirroring the audit's `_mint` vs `_safeMint` inconsistency: one "minting" path validates the target is actually usable before binding, the other does not.

### Finding Description
When a `Transfer` action targets an unused `0x…` (ETH-implicit) receiver id, the runtime creates the account via `action_implicit_account_creation_transfer`: [1](#0-0) 

This sets `account.contract = AccountContract::Global(global_contract_hash)` where `global_contract_hash = eth_wallet_global_contract_hash(chain_id)` is a hard-coded constant hash [2](#0-1)  — no lookup of `TrieKey::GlobalContractCode` is performed to confirm the corresponding WASM has actually been distributed to this shard.

Compare this with the sibling action that performs the *same* kind of binding on purpose, `use_global_contract`, which explicitly guards against a missing global contract: [3](#0-2) 

and with the documented behavior of `DeterministicStateInitAction`/`UseGlobalContractAction`, both of which surface `GlobalContractDoesNotExist` as an execution error precisely because global-contract code "propagates globally, shard by shard" and "may take a while for it to propagate to all shards": [4](#0-3) 

ETH-implicit accounts have no recovery mechanism if their bound contract is unusable: they cannot have a full-access key added, and cannot be deleted; the only way to act on them is via the Wallet Contract's `rlp_execute` method: [5](#0-4) 

If the global contract identified by `eth_wallet_global_contract_hash(chain_id)` has not yet propagated to the shard hosting a freshly-created ETH-implicit account (e.g., a shard produced by resharding right after `EthImplicitGlobalContract` activation, or any shard that has not yet received/replayed the corresponding `GlobalContractDistributionReceipt`), the account is created pointing at code that does not exist on that shard. Any `FunctionCall`/`rlp_execute` attempt against it will fail to resolve the contract, and — because the account type forbids `AddKey`/`DeleteAccount` — any balance sent along with the triggering `Transfer` (or any subsequent `Transfer`) is stranded with no code path to move, reclaim, or recover it.

### Impact Explanation
This is a permanently-frozen-funds condition triggerable by an ordinary, unprivileged transaction: any account can send a `Transfer` to an arbitrary `0x…` address at any time. If that address happens to be created on a shard where the wallet global contract hasn't yet been distributed, the deposited NEAR (and any further transfers to that same address) becomes permanently unrecoverable, since ETH-implicit accounts cannot be deleted or have keys added and rely entirely on the (missing) global contract to be usable at all.

### Likelihood Explanation
Triggering requires only a single `Transfer` to an ETH-implicit-shaped account id on a shard where the global-contract distribution receipt has not yet landed — a state that is plausible immediately after the `EthImplicitGlobalContract` feature activates, or after any resharding event that creates a shard lacking the previously-distributed global contract code (the existing `test_stale_global_contract_distribution_after_double_resharding` and `GlobalContractDistributionNonce` work show that distribution-receipt propagation across resharding boundaries is a genuinely tricky, actively-patched area). No validator collusion, network manipulation, or privileged access is required — it is reachable purely through the runtime's action-application path for a submitted transaction/receipt.

### Recommendation
Mirror the check already performed in `use_global_contract`: before binding a freshly-created ETH-implicit account to `AccountContract::Global(global_contract_hash)` in `action_implicit_account_creation_transfer`, verify `state_update.contains_key(&TrieKey::GlobalContractCode { identifier })` for the resolved hash on the current shard. If absent, either reject the implicit-creation transfer (surfacing an error analogous to `GlobalContractDoesNotExist`) or ensure the runtime/protocol guarantees the wallet global contract is present on every shard (including freshly split ones) before `EthImplicitGlobalContract`-derived account creation can occur on that shard.

### Proof of Concept
1. Enable `EthImplicitGlobalContract` (already base in the current protocol) but arrange for a shard (e.g., one created via resharding) that has not yet applied the `GlobalContractDistributionReceipt` carrying the wallet-contract code for `eth_wallet_global_contract_hash(chain_id)`.
2. Submit a `Transfer` transaction (any unprivileged signer) with the receiver id being an unused `0x…` (ETH-implicit) account id whose shard is the one from step 1, carrying a non-zero deposit.
3. `action_implicit_account_creation_transfer` creates the account with `AccountContract::Global(global_contract_hash)` without checking trie state for that shard (`runtime/runtime/src/actions.rs:255-269`).
4. Attempt to interact with the account via `rlp_execute`/`FunctionCall`: the referenced global contract code cannot be resolved on this shard.
5. Attempt `AddKey`/`DeleteAccount` on the account: both are disallowed for ETH-implicit accounts.
6. The deposited balance is now permanently inaccessible — there is no action sequence that can move or recover it.

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

**File:** runtime/runtime/src/global_contracts.rs (L76-90)
```rust
pub(crate) fn use_global_contract(
    state_update: &mut TrieUpdate,
    account_id: &AccountId,
    account: &mut Account,
    contract_identifier: &GlobalContractIdentifier,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    let key = TrieKey::GlobalContractCode { identifier: contract_identifier.clone().into() };
    if !state_update.contains_key(&key, AccessOptions::DEFAULT)? {
        result.result = Err(ActionErrorKind::GlobalContractDoesNotExist {
            identifier: contract_identifier.clone(),
        }
        .into());
        return Ok(());
    }
```

**File:** docs/RuntimeSpec/Actions.md (L452-487)
```markdown
**Outcome**:

- First, the provided code is made available as global contract on the current shard.
- The same code propagates globally, shard by shard.
- Eventually, all accounts on all shards can reference the submitted code by the corresponding global contract identifier.

### Errors

**Validation Error**:

- `ContractSizeExceeded` if the provided WebAssembly code is larger than `max_contract_size` (4MiB).

**Execution Error**:

- `LackBalanceForState` if the account does not hold enough NEAR to cover the added storage.

## UseGlobalContractAction

```rust
pub struct UseGlobalContractAction {
    /// References a deployed global contract to use in the receiver account.
    pub contract_identifier: GlobalContractIdentifier,
}
```
**Outcome**:

### Errors

**Validation Error**:

- `InvalidAccountId` if the provided account id does not follow the [AccountId specification](../DataStructures/Account.md).

**Execution Error**:

- `GlobalContractDoesNotExist` if the referenced global contract does not exist on the shard of the receiver. (It may
  take a while for it to propagate to all shards.)
```

**File:** docs/DataStructures/Account.md (L115-122)
```markdown
- If this is ETH-implicit account, it will have the [Wallet Contract](#wallet-contract) deployed, which can only be used by the owner of the Secp256K1 private key where `'0x' + keccak256(public_key)[12:32].hex()` matches the account ID.

Implicit account can not be created using `CreateAccount` action to avoid being able to hijack the account without having the corresponding private key.

Once a NEAR-implicit account is created it acts as a regular account until it's deleted.

An ETH-implicit account can only be used by calling the methods of the [Wallet Contract](#wallet-contract). It cannot be deleted, nor can a full access key be added.
The primary purpose of ETH-implicit accounts is to enable seamless integration of existing Ethereum tools (such as wallets) with the NEAR blockchain.
```
