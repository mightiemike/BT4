Confirmed finding: `validate_contract_address` in `apollo_gateway/src/stateless_transaction_validator.rs` explicitly skips the reserved-address check for `DeployAccount` transactions (`RpcTransaction::DeployAccount(_) => return Ok(())`), while `calculate_contract_address` in `starknet_api/src/core.rs` and `starknet_api/src/transaction.rs` never checks against the OS's reserved addresses (`BLOCK_HASH_TABLE_ADDRESS`=0x1, alias=0x2, reserved=0x3). Whether the blockifier's own deploy-account execution path enforces the same reserved-address guard that the Cairo OS enforces in `deploy_contract.cairo` (the `assert_not_zero(...)` check against `ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, `RESERVED_CONTRACT_ADDRESS`) could not be conclusively verified from the index — `execute_deployment` in `crates/blockifier/src/execution/execution_utils.rs` and the full `deploy_account` transaction path were not fully retrievable.

### Title
Missing reserved-address check on gateway-side DeployAccount validation may allow contract deployment collisions with OS-reserved system addresses (block hash / alias contracts) - (File: crates/apollo_gateway/src/stateless_transaction_validator.rs)

### Summary
The Starknet OS reserves contract addresses `0x1` (block hash table), `0x2` (alias contract), and `0x3` (future reserved) for internal bookkeeping, and explicitly forbids any user contract from being deployed to these addresses in `deploy_contract.cairo`: [1](#0-0) 
with the reserved constants defined here: [2](#0-1) 

However, `StatelessTransactionValidator::validate_contract_address`, which runs at the gateway (the first unprivileged-transaction-sender-reachable validation point), explicitly bypasses the sender-address range check for `DeployAccount` transactions: [3](#0-2) 
The `ContractAddress::validate()` function used elsewhere excludes only addresses `<= 0x1` from being valid "external" addresses: [4](#0-3) 
but this validation is never invoked on the *computed* deploy-account contract address (`calculate_contract_address`), which is derived purely from attacker-controlled `contract_address_salt`, `class_hash`, and `constructor_calldata`: [5](#0-4) [6](#0-5) 

### Finding Description
An attacker submitting a `DeployAccount` transaction chooses `class_hash`, `contract_address_salt`, and `constructor_calldata` freely. Since Pedersen hashing has no known preimage/collision resistance issue being exploited here structurally, but the address space is not filtered for reserved low values (`0x1`, `0x2`, `0x3`) at the gateway (bypassed by the early `return Ok(())` for `DeployAccount`), it is plausible for an attacker to iterate salts until a resulting `calculate_contract_address()` output collides with one of the reserved addresses `0x1`/`0x2`/`0x3` (this requires finding a preimage that hashes to a specific 3 small values out of a ~2^251 domain — a brute-force cost, not a structural break, but the *validation gap itself* is the root cause under audit, since the OS assumes such addresses can never be deployed to and the sequencer's gateway does not enforce that invariant before admitting the transaction into the mempool/execution pipeline). If the blockifier execution path (`execute_deployment`, called from `deploy_account` transaction execution) similarly lacks this reserved-address assertion — which could not be confirmed from available files — a successfully mined salt would let the sequencer's blockifier accept and commit a deployment to `0x1`/`0x2`/`0x3`, but the Starknet OS re-execution (used for proving) would reject it via the `assert_not_zero` in `deploy_contract.cairo`, causing a proof failure / honest-node divergence between the sequencer's committed block and the provable OS execution — analogous to the reported `querystringify` bug where an attacker-controlled key collides with a reserved/protected key that downstream code assumes is safe from user control.

### Impact Explanation
If the Rust blockifier does not mirror the OS's reserved-address assertion (unverified due to index limits), a sequencer could build and commit a block containing a deployment to a reserved system address, corrupting the alias/block-hash bookkeeping contracts' state entries or causing the block to become unprovable, which is a "network unable to confirm new transactions" / state-root divergence scenario. Even if blockifier does enforce the same check, the gateway's early bypass for `DeployAccount` represents inconsistent defense-in-depth and removes an early, cheap rejection point, meaning malicious deploy attempts are only caught late (or not at all if the Rust-side check is missing), which could be exploited to instantiate wasteful/failed-block-building near reserved addresses via the mempool and consensus flow.

### Likelihood Explanation
Finding a salt whose Pedersen hash mod the field lands exactly on `1`, `2`, or `3` out of a ~2^251 space is computationally infeasible via brute force alone, so exploiting this purely by chance is not practical. The real risk is not that likely, but the finding highlights a genuine validation asymmetry: the code assumes deploy-account target addresses can never coincide with OS-reserved values without ever verifying this assumption for this specific transaction type at the gateway.

### Recommendation
Add an explicit reserved-address check (mirroring the OS's `deploy_contract.cairo` assertion against `BLOCK_HASH_CONTRACT_ADDRESS` (0x1), `ALIAS_CONTRACT_ADDRESS` (0x2), and `RESERVED_CONTRACT_ADDRESS` (0x3)) both in `StatelessTransactionValidator::validate_contract_address` for `DeployAccount` transactions and in the blockifier's deploy execution path (`execute_deployment`/`syscall_base.rs::deploy`), so the invariant is enforced consistently at every point where a contract address is computed from user-controlled inputs, not only inside the Cairo OS program.

### Proof of Concept
Not fully constructible from the indexed code: the exploit would require (1) confirming whether `crates/blockifier/src/execution/execution_utils.rs::execute_deployment` and the `DeployAccount` transaction execution path perform the same reserved-address assertion as `deploy_contract.cairo`, and (2) a brute-force salt search to land on address `0x1`/`0x2`/`0x3`, which was not verified within this analysis due to index size limits on some files. A Devin session with full repository access would be needed to inspect `execute_deployment` and the `DeployAccountTransaction::execute` path in `crates/blockifier/src/transaction/` to confirm or rule out the presence/absence of the reserved-address guard on the Rust execution side.

### Citations

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L44-49)
```text
    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );
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

**File:** crates/starknet_api/src/core.rs (L269-282)
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

**File:** crates/starknet_api/src/transaction.rs (L459-474)
```rust
impl<T: DeployTransactionTrait> CalculateContractAddress for T {
    /// Calculates the contract address for the contract deployed by a deploy account transaction.
    /// For more details see:
    /// <https://docs.starknet.io/learn/cheatsheets/transactions-reference#deploy-account-v3>
    fn calculate_contract_address(&self) -> StarknetApiResult<ContractAddress> {
        // When the contract is deployed via a deploy-account transaction, the deployer address is
        // zero.
        const DEPLOYER_ADDRESS: ContractAddress = ContractAddress(PatriciaKey::ZERO);
        calculate_contract_address(
            self.contract_address_salt(),
            self.class_hash(),
            self.constructor_calldata(),
            DEPLOYER_ADDRESS,
        )
    }
}
```
