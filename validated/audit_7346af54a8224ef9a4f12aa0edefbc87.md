### Title
Contract address validation fails to block OS-reserved alias/reserved addresses (0x2, 0x3), analogous to incomplete reserved-address filtering — (File: crates/starknet_api/src/core.rs)

### Summary
`ContractAddress::validate()` is documented as ensuring an address is "above the special saved addresses," but its actual check only excludes `BLOCK_HASH_TABLE_ADDRESS` (`0x1`) and `0x0`, while the OS reserves additional low addresses — `ALIAS_CONTRACT_ADDRESS = 0x2` and `RESERVED_CONTRACT_ADDRESS = 0x3` — that are never checked against. This mirrors the Stunnel bug class: a security-relevant filter intends to reject a *category* of special/internal-only values but only rejects a strict subset of the equivalent/adjacent representations, letting the rest slip through the same reachable path (here, a user-supplied `sender_address` on an `Invoke`/`Declare` transaction, validated in the gateway).

### Finding Description
`ContractAddress::validate()` at [1](#0-0)  checks only:
```
if (value > BLOCK_HASH_TABLE_ADDRESS.0.0) && (value < l2_address_upper_bound) { Ok(()) } else { Err(...) }
```
i.e. it excludes exactly `0x0` and `0x1`. But the OS constants file explicitly reserves more addresses as "OS reserved contract addresses": `BLOCK_HASH_CONTRACT_ADDRESS = 0x1`, `ALIAS_CONTRACT_ADDRESS = 0x2` (used for stateful-compression aliasing), and `RESERVED_CONTRACT_ADDRESS = 0x3` ("future reserved contract address") — see [2](#0-1)  and the mirrored Rust versioned-constants JSON (`os_contract_addresses`) fields referenced throughout `blockifier_versioned_constants_*.json`.

This validation is invoked by the gateway's stateless validator on the transaction's user-supplied `sender_address` for `Invoke`/`Declare` transactions: [3](#0-2) . The address `0x2` is exactly the `alias_contract_address` that the Starknet OS internally reads/writes when `enable_stateful_compression` is on, to track address/storage-key aliasing used to compress the state diff and compute the committed state root (`allocate_aliases_in_storage`, `AliasUpdater` in [4](#0-3)  and the corresponding OS Cairo logic in [5](#0-4) ).

### Impact Explanation
This is the exact bug class analog to the CVE: a filter meant to reject a whole category of reserved/internal-only targets (loopback addresses vs. OS-reserved contract addresses) is implemented as a narrow numeric range check that omits some members of that category (`0x2`, `0x3`) while explicitly documenting the intent to exclude "the special saved addresses" (plural). If reachability could be established (e.g., contract deployment or L1-handler dispatch landing exactly on `0x2`/`0x3`), colliding user-controlled state changes with the OS's internal alias-bookkeeping storage would corrupt `allocate_aliases_in_storage`/`compress`/`decompress` logic, potentially producing a wrong committed state root or an OS/blockifier state-diff divergence between re-execution paths.

### Likelihood Explanation
Low/uncertain reachability could not be fully confirmed in this session. Contract addresses for `Invoke`/`Declare` sender fields are user-supplied felts (not hash-derived) and pass this validation if equal to `0x2` or `0x3`, but subsequent stateful validation (`get_nonce_from_state`) still requires a deployed class at that address ( [6](#0-5) ), which is not naturally achievable since contract deployment addresses are Pedersen-hash-derived ( [7](#0-6) ) and hitting `0x2`/`0x3` exactly is computationally infeasible. I was not able to verify within the available time whether `DeployAccount`'s `validate_contract_address` (which unconditionally returns `Ok(())`, see [3](#0-2) ) or any L1-handler `contract_address` path (`crates/blockifier/src/transaction/l1_handler_transaction.rs`) could otherwise cause execution/state writes to land at these reserved addresses without a hash-preimage requirement.

### Recommendation
Extend `ContractAddress::validate()` (and any equivalent checks) to explicitly reject the full set of OS-reserved addresses (`BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, `RESERVED_CONTRACT_ADDRESS`, and any future additions) rather than a single hardcoded threshold, ideally by referencing the canonical `os_contract_addresses` constants so the validator and the OS reserved-address list cannot drift out of sync.

### Proof of Concept
Not established as end-to-end exploitable in this session — reachability to actually get user-controlled state changes committed at address `0x2`/`0x3` was not confirmed (deployment addresses are hash-derived, and stateful validation still requires a deployed class at the sender address). Recommend a background Devin session with full repo/tool access to check: (1) whether `DeployAccount`'s address-validation bypass combined with any genesis/test-config deployment could place a class at `0x2`/`0x3`, and (2) whether L1-handler `contract_address` (attacker-controlled from L1) can reach state-diff writes to these addresses without needing a pre-deployed class, to determine true exploitability before treating this as a confirmed vulnerability.

### Citations

**File:** crates/starknet_api/src/core.rs (L269-281)
```rust
impl ContractAddress {
    /// Validates the contract address is in the valid range for external access.
    /// The lower bound is above the special saved addresses and the upper bound is congruent with
    /// the storage var address upper bound.
    pub fn validate(&self) -> Result<(), StarknetApiError> {
        let value = self.0.0;
        let l2_address_upper_bound = Felt::from(*L2_ADDRESS_UPPER_BOUND);
        if (value > BLOCK_HASH_TABLE_ADDRESS.0.0) && (value < l2_address_upper_bound) {
            return Ok(());
        }

        Err(StarknetApiError::OutOfRange { string: format!("[0x2, {l2_address_upper_bound})") })
    }
```

**File:** crates/starknet_api/src/core.rs (L326-346)
```rust
pub fn calculate_contract_address(
    salt: ContractAddressSalt,
    class_hash: ClassHash,
    constructor_calldata: &Calldata,
    deployer_address: ContractAddress,
) -> Result<ContractAddress, StarknetApiError> {
    let constructor_calldata_hash = Pedersen::hash_array(&constructor_calldata.0);
    let contract_address_prefix = format!("0x{}", hex::encode(CONTRACT_ADDRESS_PREFIX));
    let address = Pedersen::hash_array(&[
        Felt::from_hex(contract_address_prefix.as_str()).map_err(|_| {
            StarknetApiError::OutOfRange { string: contract_address_prefix.clone() }
        })?,
        *deployer_address.0.key(),
        salt.0,
        class_hash.0,
        constructor_calldata_hash,
    ]);
    let (_, address) = address.div_rem(&L2_ADDRESS_UPPER_BOUND);

    ContractAddress::try_from(address)
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L56-65)
```text
// OS reserved contract addresses.

// This contract stores the block number -> block hash mapping.
const BLOCK_HASH_CONTRACT_ADDRESS = 0x1;
// This contract stores the aliases mapping used for stateful compression.
const ALIAS_CONTRACT_ADDRESS = 0x2;
// Future reserved contract address.
const RESERVED_CONTRACT_ADDRESS = 0x3;
// The block number -> block hash mapping is written for the current block number minus this number.
const STORED_BLOCK_HASH_BUFFER = 10;
```

**File:** crates/apollo_gateway/src/stateless_transaction_validator.rs (L90-98)
```rust
    fn validate_contract_address(tx: &RpcTransaction) -> StatelessTransactionValidatorResult<()> {
        let sender_address = match tx {
            RpcTransaction::Declare(RpcDeclareTransaction::V3(tx)) => tx.sender_address,
            RpcTransaction::DeployAccount(_) => return Ok(()),
            RpcTransaction::Invoke(RpcInvokeTransaction::V3(tx)) => tx.sender_address,
        };

        Ok(sender_address.validate()?)
    }
```

**File:** crates/blockifier/src/state/stateful_compression.rs (L46-80)
```rust
/// Allocates aliases for the new addresses and storage keys in the alias contract.
/// Iterates over the addresses in ascending order. For each address, sets an alias for the new
/// storage keys (in ascending order) and for the address itself.
pub fn allocate_aliases_in_storage<S: StateReader>(
    state: &mut CachedState<S>,
    alias_contract_address: ContractAddress,
) -> StateResult<()> {
    let state_diff = state.to_state_diff()?.state_maps;

    // Collect the contract addresses and the storage keys that need aliases.
    let contract_addresses: BTreeSet<ContractAddress> =
        state_diff.get_contract_addresses().into_iter().collect();
    let mut contract_address_to_sorted_storage_keys = HashMap::new();
    for (contract_address, storage_key) in state_diff.storage.keys() {
        if contract_address > &MAX_NON_COMPRESSED_CONTRACT_ADDRESS {
            contract_address_to_sorted_storage_keys
                .entry(contract_address)
                .or_insert_with(BTreeSet::new)
                .insert(storage_key);
        }
    }

    // Iterate over the addresses and the storage keys and update the aliases.
    let mut alias_updater = AliasUpdater::new(state, alias_contract_address)?;
    for contract_address in contract_addresses {
        if let Some(storage_keys) = contract_address_to_sorted_storage_keys.get(&contract_address) {
            for key in storage_keys {
                alias_updater.insert_alias(key)?;
            }
        }
        alias_updater.insert_alias(&StorageKey(contract_address.0))?;
    }

    alias_updater.finalize_updates()
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/state/aliases.cairo (L192-242)
```text
// Returns whether the contract at the given address should be skipped when assigning/replacing
// aliases.
func should_skip_contract{range_check_ptr}(contract_address: felt) -> felt {
    alloc_locals;
    local contract_address_le_max_for_compression;
    %{ ContractAddressLeMaxForCompression %}
    if (contract_address_le_max_for_compression != FALSE) {
        // Don't give any aliases for contracts <= MAX_NON_COMPRESSED_CONTRACT_ADDRESS.
        assert_nn_le(a=contract_address, b=MAX_NON_COMPRESSED_CONTRACT_ADDRESS);
        return TRUE;
    }
    assert_le_felt(a=MAX_NON_COMPRESSED_CONTRACT_ADDRESS + 1, b=contract_address);
    return FALSE;
}

// Allocates aliases for contract state diff, which is expected to contain only modified
// contracts and storage keys, without trivial updates.
func allocate_aliases_for_contract_state_diff{
    aliases_storage_updates: DictAccess*, next_available_alias: felt, range_check_ptr
}(n_contracts: felt, contract_state_diff: felt*) {
    if (n_contracts == 0) {
        return ();
    }
    alloc_locals;

    let contract_header = cast(contract_state_diff, FullContractHeader*);
    local contract_address = contract_header.address;
    local storage_diff_start: FullStateUpdateEntry* = cast(
        &contract_state_diff[FullContractHeader.SIZE], FullStateUpdateEntry*
    );
    local storage_diff_end: FullStateUpdateEntry* = &storage_diff_start[
        contract_header.n_storage_diffs
    ];
    let skip_contract = should_skip_contract(contract_address=contract_address);
    if (skip_contract != FALSE) {
        return allocate_aliases_for_contract_state_diff(
            n_contracts=n_contracts - 1, contract_state_diff=storage_diff_end
        );
    }

    // Allocate for the storage diff.
    allocate_aliases_for_storage_diff(
        storage_diff_start=storage_diff_start, storage_diff_end=storage_diff_end
    );
    // Allocate for the contract address.
    maybe_allocate_alias_for_key(key=contract_address);

    return allocate_aliases_for_contract_state_diff(
        n_contracts=n_contracts - 1, contract_state_diff=storage_diff_end
    );
}
```

**File:** crates/apollo_gateway/src/stateful_transaction_validator.rs (L158-176)
```rust
    async fn extract_state_nonce_and_run_validations(
        &mut self,
        executable_tx: &ExecutableTransaction,
        mempool_client: SharedMempoolClient,
    ) -> StatefulTransactionValidatorResult<Nonce> {
        let account_nonce =
            self.get_nonce_from_state(executable_tx.contract_address()).await.map_err(|e| {
                // TODO(noamsp): Fix this. Need to map the errors better.
                StarknetError::internal_with_signature_logging(
                    format!(
                        "Failed to get nonce for sender address {}",
                        executable_tx.contract_address()
                    ),
                    &executable_tx.signature(),
                    e,
                )
            })?;
        let skip_validate =
            self.run_pre_validation_checks(executable_tx, account_nonce, mempool_client).await?;
```
