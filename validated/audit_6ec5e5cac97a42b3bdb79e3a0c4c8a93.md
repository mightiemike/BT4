Based on my investigation, I found a genuine analog: `ContractAddress::validate()` deliberately excludes only the OS's reserved address (`0x1`), but the OS reserves **four** special addresses (`0`, `1`, `2`, `3`), and `validate_contract_address` in the gateway only calls this check on the `sender_address` — an incomplete range check reused across contexts where the full reserved set matters.

### Title
Gateway/API contract-address validation only excludes one of four OS-reserved addresses, allowing acceptance of addresses meant to be reserved - ([File: crates/starknet_api/src/core.rs])

### Summary
`ContractAddress::validate()` bounds-checks an address against a single constant, `BLOCK_HASH_TABLE_ADDRESS` (`0x1`), while the Starknet OS actually reserves four special addresses: `0` (`ORIGIN_ADDRESS`), `1` (`BLOCK_HASH_CONTRACT_ADDRESS`), `2` (`ALIAS_CONTRACT_ADDRESS`), and `3` (`RESERVED_CONTRACT_ADDRESS`). This mirrors the Notional report's pattern: a denylist/validation routine that is supposed to fully exclude a set of "special" values but instead checks against an incomplete or wrong constant, letting disallowed values slip through the gate that is supposed to stop them.

### Finding Description
`ContractAddress::validate` is defined as: [1](#0-0) 

It only rejects addresses `<= BLOCK_HASH_TABLE_ADDRESS` (`0x1`), so the value `2` (`ALIAS_CONTRACT_ADDRESS`) and `3` (`RESERVED_CONTRACT_ADDRESS`) pass this check as "valid" contract addresses.

Meanwhile, the Starknet OS Cairo code defines and actively guards against contract deployment onto all four reserved addresses: [2](#0-1) [3](#0-2) 

The gateway calls `ContractAddress::validate()` on the transaction's `sender_address` for `Declare`/`Invoke` transactions: [4](#0-3) 

Because this Rust-level check only excludes `0` and `1` but not `2`/`3`, any place in the sequencer/gateway/RPC pipeline that relies on `ContractAddress::validate()` as the authoritative guard against "reserved OS addresses" is effectively only defending against 2 of the 4 reserved slots, exactly analogous to the Notional bug where `_isInvalidRewardToken` checked the wrong ETH sentinel and let ETH slip through the denylist.

### Impact Explanation
If a transaction is accepted by the gateway with `sender_address == ALIAS_CONTRACT_ADDRESS (2)` or `RESERVED_CONTRACT_ADDRESS (3)` because the Rust-side gate does not reject it, and this value is later relied upon anywhere in blockifier execution or state commitment (e.g., contract state changes keyed by this address, or the aliasing mechanism in `crates/blockifier/src/state/stateful_compression.rs` and the OS `aliases.cairo`, which specifically treat `ALIAS_CONTRACT_ADDRESS` as the special dictionary used for stateful compression), it could corrupt the aliasing bookkeeping used for state-diff compression, producing a state root/commitment mismatch between the sequencer's blockifier execution and the OS's re-execution (which does enforce the full reserved-address exclusion). This is a state-commitment/divergence class of impact.

### Likelihood Explanation
Likelihood depends on whether any code path *besides* `deploy_contract.cairo`'s already-correct Cairo-side check relies on the Rust `ContractAddress::validate()` as its sole protection. In the code paths found, `validate()` is invoked only on `sender_address` in gateway pre-checks — an already-deployed account address cannot generally equal `2` or `3` because deployment itself is blocked by the correct Cairo-side guard. This significantly limits real-world exploitability, since an attacker would need to first get a contract legitimately deployed/aliased at address `2`/`3`, which the deploy-time OS check prevents. I could not find a concrete state-changing bypass path using only this gap; it is a defense-in-depth inconsistency rather than a demonstrated exploitable divergence.

### Recommendation
Align `ContractAddress::validate()` with the full set of OS-reserved addresses (`ORIGIN_ADDRESS`, `BLOCK_HASH_CONTRACT_ADDRESS`, `ALIAS_CONTRACT_ADDRESS`, `RESERVED_CONTRACT_ADDRESS`) instead of only `BLOCK_HASH_TABLE_ADDRESS`, or explicitly document why the current bound is sufficient and audit every caller (gateway, RPC, batcher) to confirm none of them rely on this function to exclude the alias/reserved addresses.

### Proof of Concept
Not conclusively demonstrated. I confirmed the constant mismatch: [1](#0-0)  vs. the four-address reservation in [3](#0-2) , but I was unable to find a call path where a single submitted transaction can set a contract's own address to `2`/`3` without first passing the Cairo-level deploy guard, which correctly checks all four values. Given the ask-only constraints and remaining uncertainty about a concrete exploitation chain, this should be treated as a defense-in-depth gap flagged for further investigation rather than a confirmed, independently-exploitable vulnerability.

### Citations

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

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants_template.txt (L44-51)
```text
// OS reserved contract addresses.

// This contract stores the block number -> block hash mapping.
const BLOCK_HASH_CONTRACT_ADDRESS = {BLOCK_HASH_CONTRACT_ADDRESS};
// This contract stores the aliases mapping used for stateful compression.
const ALIAS_CONTRACT_ADDRESS = {ALIAS_CONTRACT_ADDRESS};
// Future reserved contract address.
const RESERVED_CONTRACT_ADDRESS = {RESERVED_CONTRACT_ADDRESS};
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/deploy_contract.cairo (L42-49)
```text
    local contract_address = constructor_execution_context.execution_info.contract_address;

    // Assert that we don't deploy to one of the reserved addresses.
    assert_not_zero(
        (contract_address - ORIGIN_ADDRESS) * (contract_address - BLOCK_HASH_CONTRACT_ADDRESS) * (
            contract_address - ALIAS_CONTRACT_ADDRESS
        ) * (contract_address - RESERVED_CONTRACT_ADDRESS),
    );
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
