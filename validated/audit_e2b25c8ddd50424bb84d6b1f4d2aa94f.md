### Title
Hardcoded per-chain `eth_wallet_global_contract_hash` constant can desynchronize from the actual deployed Wallet Contract global code, permanently bricking every ETH-implicit account - (File: `runtime/near-wallet-contract/src/lib.rs`)

### Summary
`eth_wallet_global_contract_hash(chain_id)` in `runtime/near-wallet-contract/src/lib.rs:89-105` hardcodes literal `CryptoHash` byte arrays for `MAINNET`/`MOCKNET` and `TESTNET`, which are used by `action_implicit_account_creation_transfer` (`runtime/runtime/src/actions.rs:255-269`) to stamp every newly created ETH-implicit account with `AccountContract::Global(global_contract_hash)`. This is directly analogous to the reported bug class: a hardcoded constant identifier that must exactly match an externally-deployed artifact (there, the Uniswap V2 router address; here, the on-chain global-contract code hash of the Wallet Contract). If the constant does not match the code hash of the contract actually distributed under `TrieKey::GlobalContractCode { identifier: CodeHash(hash) }`, every ETH-implicit account created against that constant becomes permanently non-functional.

### Finding Description
Any unprivileged user can trigger ETH-implicit account creation with an ordinary `Transfer` action to an address matching `0x` + 40 hex chars (`action_implicit_account_creation_transfer`, `runtime/runtime/src/actions.rs:221-269`). When `EthImplicitGlobalContract` is active, this path does not deploy or verify anything — it merely writes:
```
let global_contract_hash = eth_wallet_global_contract_hash(&chain_id);
*account = Some(Account::new(deposit, Balance::ZERO,
    AccountContract::Global(global_contract_hash), storage_usage));
``` [1](#0-0) 

`eth_wallet_global_contract_hash` returns hardcoded byte arrays for mainnet/testnet that are supposed to equal the hash of the Wallet Contract WASM actually deployed as a global contract on that network via a separate `DeployGlobalContract` transaction: [2](#0-1) 

There is no runtime check tying this constant to the actually-deployed global contract code — the only "proof" in the codebase is a self-referential unit test (`test_eth_wallet_global_contract_hash_values`) that compares the function's output against the same literal values, not against the hash of the shipped WASM (`res/wallet_contract_mainnet.wasm` / `res/wallet_contract_testnet.wasm`): [3](#0-2) 

When a contract call is later made against such an account (e.g. a relayer's `rlp_execute` FunctionCall), the runtime resolves the account's global contract code by looking up `TrieKey::GlobalContractCode { identifier: CodeHash(hash) }` (`runtime/runtime/src/contract_code.rs`, `GlobalContractAccessExt::hash`/`code`). If no such entry exists in the trie (because the wrong/never-deployed hash was hardcoded), `near_vm_runner::run` returns `VMRunnerError::ContractCodeNotPresent`, which `execute_function_call` (`runtime/runtime/src/function_call.rs:290-313`) turns into `FunctionCallError::CompilationError(CompilationError::CodeDoesNotExist)` — a deterministic, permanent failure for every single call on every ETH-implicit account on that chain. [4](#0-3) 

### Impact Explanation
Because the account is created as `AccountContract::Global(<hash>)` at creation time (not lazily resolved), the wrong hash is baked into every ETH-implicit account irreversibly. ETH-implicit accounts cannot be deleted or have a full-access key added, and can only be operated through the Wallet Contract's `rlp_execute` method (per NEP-518, `docs/DataStructures/Account.md:121-129`). If the hardcoded hash is stale or wrong for a network, then:
- No transaction can ever successfully invoke `rlp_execute` (or any function) on any ETH-implicit account on that chain — the class of accounts is completely and permanently bricked network-wide (`CompilationError::CodeDoesNotExist` on every call).
- Any funds sent to ETH-implicit accounts prior to or after this mismatch become permanently frozen, since there is no other supported way to move funds out of such an account.
- This is worse in class than the reference finding (unusable Uniswap V2) because it is unrecoverable per-account without a further protocol change (the account's `AccountContract::Global` binding does not get re-resolved once set), rather than merely reverting a swap call.

### Likelihood Explanation
Reaching this bug requires no privilege: any signer can create an ETH-implicit account by simply sending a zero- or non-zero-value `Transfer` to a valid `0x...` address (as shown in `integration-tests/src/tests/features/wallet_contract.rs:92-129`), and any relayer/user can then attempt an `rlp_execute` call. The vulnerability is entirely a maintenance/consistency risk between the hardcoded `CryptoHash` literals in `near-wallet-contract/src/lib.rs` and whatever Wallet Contract code hash is actually published on-chain as a `DeployGlobalContract` with `GlobalContractDeployMode::CodeHash`. Because the constant is compiled into `neard` and is not cross-checked against the genesis/global-contract state at startup or at account-creation time, any future update to the Wallet Contract WASM, any accidental copy/paste of the wrong hash between mainnet/testnet, or any deployment-vs-hardcode ordering mistake at protocol activation would silently and deterministically brick this feature network-wide, only surfacing when someone actually calls the account. This mirrors exactly the root cause of the referenced report (a compiled-in address/hash constant for an external dependency, unverified against the live deployment).

### Recommendation
- Do not stamp `AccountContract::Global` with a hardcoded hash whose correctness is unverifiable at compile/runtime; instead verify, at node startup or at protocol-feature activation, that a global contract with `eth_wallet_global_contract_hash(chain_id)` exists in state, panicking early (as is already done for other frozen genesis invariants, e.g. `core/store/src/genesis/initialization.rs`) rather than allowing silent creation of unusable accounts.
- Derive the constant from the actual embedded WASM hash (`ContractCode::hash()`) for all chains, the same way `LOCALNET` already does (`_ => *LOCALNET.read_contract().hash()`), instead of hand-copied literal byte arrays for `MAINNET`/`TESTNET`, eliminating the possibility of drift between the embedded/deployed contract and the hardcoded value.
- Add an integration/consistency test that deploys the exact `res/wallet_contract_mainnet.wasm` / `res/wallet_contract_testnet.wasm` bytes as a global contract and asserts the resulting on-chain code hash equals `eth_wallet_global_contract_hash(MAINNET/TESTNET)`, rather than only comparing the function's literals to themselves.

### Proof of Concept
Conceptual reproduction (cannot be executed without genesis/deployment state, but demonstrates root cause deterministically from the code):
1. Suppose the constant returned by `eth_wallet_global_contract_hash(TESTNET)` (`runtime/near-wallet-contract/src/lib.rs:97-102`) does not match the code hash of the Wallet Contract WASM actually distributed via `DeployGlobalContract{CodeHash}` on testnet (e.g., due to a WASM rebuild without updating the literal, as already happened once historically per the `OLD_TESTNET` variant/comment: "Initial version of WalletContract... we still use this one on testnet protocol version 70").
2. Any user sends `SignedTransaction::send_money(nonce, signer, eth_implicit_account_id, ..., deposit, block_hash)` to a `0x...` account — this succeeds and creates the account with `AccountContract::Global(<mismatched_hash>)` per `action_implicit_account_creation_transfer` (`runtime/runtime/src/actions.rs:255-269`).
3. A relayer later submits any `FunctionCall` (e.g. `rlp_execute`) against that account. `execute_function_call` calls `near_vm_runner::run`, which cannot find code under `TrieKey::GlobalContractCode{CodeHash(<mismatched_hash>)}`, returning `VMRunnerError::ContractCodeNotPresent`.
4. This is converted to `FunctionCallError::CompilationError(CompilationError::CodeDoesNotExist)` (`runtime/runtime/src/function_call.rs:309-312`), permanently failing every call to every ETH-implicit account on that chain, with no remediation path since the account cannot be deleted or re-keyed (per the ETH-implicit account restrictions documented in `docs/DataStructures/Account.md:121`). [5](#0-4) [6](#0-5) [4](#0-3)

### Citations

**File:** runtime/runtime/src/actions.rs (L253-269)
```rust
        // Invariant: The `account_id` is implicit.
        // It holds because in the only calling site, we've checked the permissions before.
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

**File:** runtime/near-wallet-contract/src/lib.rs (L158-202)
```rust
#[cfg(test)]
mod tests {
    use crate::{
        OLD_TESTNET, code_hash_matches_wallet_contract, eth_wallet_global_contract_hash,
        wallet_contract_magic_bytes,
    };
    use near_primitives_core::{
        chains::{MAINNET, MOCKNET, TESTNET},
        hash::CryptoHash,
    };
    use std::str::FromStr;

    #[test]
    fn test_code_hash_matches_wallet_contract() {
        let chain_ids = [MAINNET, TESTNET, "localnet"];
        let testnet_code_v70 = OLD_TESTNET.magic_bytes();
        let other_code_hash =
            CryptoHash::from_str("9rmLr4dmrg5M6Ts6tbJyPpbCrNtbL9FCdNv24FcuWP5a").unwrap();
        for id in chain_ids {
            assert!(
                code_hash_matches_wallet_contract(id, wallet_contract_magic_bytes(id).hash()),
                "Wallet contract magic bytes matches wallet contract"
            );
            assert_eq!(
                code_hash_matches_wallet_contract(id, testnet_code_v70.hash()),
                id == TESTNET,
                "Special case only matches on testnet"
            );
            assert!(
                !code_hash_matches_wallet_contract(id, &other_code_hash),
                "Other code hashes do not match wallet contract"
            );
        }
    }

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

**File:** runtime/runtime/src/function_call.rs (L290-313)
```rust
    let mut outcome = match result {
        Err(VMRunnerError::ContractCodeNotPresent) => {
            if runtime_ext.account().contract().is_some() {
                debug_assert!(
                    apply_state.apply_reason != ApplyChunkReason::UpdateTrackedShard,
                    "inconsistent state: contract code is missing from the trie, but the account has a non-empty contract"
                );

                // A missing body for an account that commits to a code hash is
                // witness incompleteness, not an execution result. Fail like any
                // other missing witness value rather than treating it as no-op.
                if apply_state.apply_reason == ApplyChunkReason::ValidateChunkStateWitness {
                    return Err(StorageError::MissingTrieValue(MissingTrieValue {
                        context: MissingTrieValueContext::TrieMemoryPartialStorage,
                        hash: contract_code_hash,
                    })
                    .into());
                }
            }
            let error = FunctionCallError::CompilationError(CompilationError::CodeDoesNotExist {
                account_id: account_id.as_str().into(),
            });
            return Ok(VMOutcome::nop_outcome(error));
        }
```
