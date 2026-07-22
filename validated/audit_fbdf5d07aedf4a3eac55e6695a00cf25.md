### Title
`compute_meta_tx_v0_hash` Shares Hash Domain with `InvokeTransactionV0`, Enabling Cross-Context Signature Replay — (File: `crates/blockifier/src/execution/syscalls/syscall_base.rs`, `crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo`)

---

### Summary

The `meta_tx_v0` syscall computes its transaction hash using the exact same `INVOKE_HASH_PREFIX`, version field (`0`), and Pedersen hash structure as a real `InvokeTransactionV0` with `max_fee = 0`. No domain separator distinguishes a meta-transaction hash from a real on-chain transaction hash. A signature produced for a real `InvokeTransactionV0` with `max_fee = 0` and `entry_point_selector = EXECUTE_ENTRY_POINT_SELECTOR` is byte-for-byte identical to the hash the `meta_tx_v0` syscall presents to the target account's `__execute__` function for the same `(contract_address, calldata, chain_id)` tuple.

---

### Finding Description

**Rust blockifier path** — `syscall_base.rs`:

```rust
let transaction_hash = InvokeTransactionV0 {
    max_fee: Fee(0),
    signature: signature.clone(),
    contract_address,
    entry_point_selector,   // always EXECUTE_ENTRY_POINT_SELECTOR
    calldata,
}
.calculate_transaction_hash(
    &self.context.tx_context.block_context.chain_info.chain_id,
    &signed_tx_version(&TransactionVersion::ZERO, &TransactionOptions { only_query }),
)?;
``` [1](#0-0) 

This calls `get_invoke_transaction_v0_hash` → `get_common_invoke_transaction_v0_hash(is_deprecated=false)`, which produces:

```
H_pedersen(INVOKE, 0x0, contract_address, EXECUTE_SELECTOR, H(calldata), 0, chain_id)
``` [2](#0-1) 

**Cairo OS path** — `transaction_hash.cairo`:

```cairo
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(...) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=INVOKE_HASH_PREFIX,
        version=0,
        ...
        max_fee=0,
        ...
    );
``` [3](#0-2) 

Both paths use `INVOKE_HASH_PREFIX` (`'invoke'`) with `version = 0` and `max_fee = 0`. [4](#0-3) 

A real `InvokeTransactionV0` with `max_fee = 0` and `entry_point_selector = EXECUTE_ENTRY_POINT_SELECTOR` produces the **identical** hash:

```
H_pedersen(INVOKE, 0x0, contract_address, EXECUTE_SELECTOR, H(calldata), 0, chain_id)
``` [5](#0-4) 

There is no domain constant, type tag, or structural field that separates the meta-transaction hash space from the real transaction hash space. The analog to the `burn()`/`move()` report is exact: two distinct operations (`meta_tx_v0` execution vs. real `InvokeTransactionV0` sequencing) share the same signature preimage, with only the `max_fee` field value (`0` vs. non-zero) providing any separation — and `meta_tx_v0` always fixes `max_fee = 0`, collapsing that separation entirely.

The `meta_tx_v0` syscall also sets `nonce = 0` and does not increment the account nonce:

```rust
let new_tx_info = TransactionInfo::Deprecated(DeprecatedTransactionInfo {
    common_fields: CommonAccountFields {
        transaction_hash,
        version: TransactionVersion::ZERO,
        nonce: Nonce(0.into()),   // nonce bypassed
        ...
    },
    max_fee: Fee(0),
});
``` [6](#0-5) 

---

### Impact Explanation

An account contract whose `__validate__` or `__execute__` function authenticates the caller by checking an ECDSA signature against `tx_info.transaction_hash` (the standard pattern) will accept a signature originally produced for a real `InvokeTransactionV0` with `max_fee = 0` when that same signature is replayed inside a `meta_tx_v0` syscall. Because `meta_tx_v0` bypasses the nonce increment, the replay does not consume the account's nonce, and the account's `__execute__` function runs with attacker-controlled calldata under the victim account's identity. This maps to the allowed impact: **"Invalid or unauthorized Starknet transaction accepted through account validation, signature … logic"** and **"Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."**

---

### Likelihood Explanation

`InvokeTransactionV0` is a deprecated transaction type. A `max_fee = 0` value on a real V0 transaction would normally be rejected by the gateway before sequencing. However:

- The gateway's fee check is a mempool/admission gate, not a cryptographic invariant; the hash collision exists at the protocol level regardless.
- Starknet's feeder gateway and state-sync path can replay historical V0 transactions with `max_fee = 0` (they exist on mainnet from the pre-fee era).
- Any off-chain signing tool or SDK that produces a V0 signature for testing or simulation with `max_fee = 0` creates a replayable credential.
- The `only_query` flag propagates into the version field via `signed_tx_version`, so query-mode V0 hashes also collide with query-mode meta_tx_v0 hashes. [7](#0-6) 

---

### Recommendation

Introduce a dedicated domain tag for meta-transaction hashes. Replace `INVOKE_HASH_PREFIX` in `compute_meta_tx_v0_hash` with a new constant such as `META_TX_V0_HASH_PREFIX = 'meta_tx_v0'`. Apply the same change in the Rust `syscall_base.rs` path by constructing a bespoke hash chain rather than delegating to `InvokeTransactionV0::calculate_transaction_hash`. This mirrors the recommendation in the external report: include the "function selector" (here, the operation type) in the signed preimage so that a signature authorizing one operation cannot be presented as authorization for a structurally identical but semantically different operation.

---

### Proof of Concept

1. Account `A` at address `addr` previously signed a real `InvokeTransactionV0`:
   - `max_fee = 0`, `entry_point_selector = EXECUTE_ENTRY_POINT_SELECTOR`, `calldata = [X]`
   - Signature `(r, s)` over `H(INVOKE, 0, addr, EXECUTE_SELECTOR, H([X]), 0, chain_id)`

2. Attacker deploys a contract `C` that calls the `meta_tx_v0` syscall:
   ```
   meta_tx_v0(contract_address=addr, selector=EXECUTE_SELECTOR, calldata=[X], signature=[(r,s)])
   ```

3. The sequencer (Rust `syscall_base.rs`) computes:
   ```
   meta_tx_hash = H(INVOKE, 0, addr, EXECUTE_SELECTOR, H([X]), 0, chain_id)
   ```
   — identical to step 1.

4. Account `A`'s `__execute__` is invoked with `tx_info.transaction_hash = meta_tx_hash`, `tx_info.signature = (r, s)`, `tx_info.nonce = 0`.

5. Account `A`'s signature check passes (`check_ecdsa_signature(meta_tx_hash, pubkey_A, r, s) == true`).

6. `__execute__` runs with calldata `[X]` under account `A`'s identity. Account `A`'s nonce is **not** incremented. The original signature has been replayed in a context it was never intended to authorize. [8](#0-7) [9](#0-8)

### Citations

**File:** crates/blockifier/src/execution/syscalls/syscall_base.rs (L286-367)
```rust
    pub fn meta_tx_v0(
        &mut self,
        contract_address: ContractAddress,
        entry_point_selector: EntryPointSelector,
        calldata: Calldata,
        signature: TransactionSignature,
        remaining_gas: &mut u64,
    ) -> SyscallResult<Vec<Felt>> {
        self.increment_syscall_linear_factor_by(&SyscallSelector::MetaTxV0, calldata.0.len());
        if self.context.execution_mode == ExecutionMode::Validate {
            self.reject_syscall_in_validate_mode("meta_tx_v0")?;
        }
        if entry_point_selector != selector_from_name(EXECUTE_ENTRY_POINT_NAME) {
            return Err(SyscallExecutionError::Revert { error_data: vec![INVALID_ARGUMENT_FELT] });
        }
        let entry_point = CallEntryPoint {
            class_hash: None,
            code_address: Some(contract_address),
            entry_point_type: EntryPointType::External,
            entry_point_selector,
            calldata: calldata.clone(),
            storage_address: contract_address,
            caller_address: ContractAddress::default(),
            call_type: CallType::Call,
            // NOTE: this value might be overridden later on.
            initial_gas: *remaining_gas,
        };

        let old_tx_context = self.context.tx_context.clone();
        let only_query = old_tx_context.tx_info.only_query();

        // Compute meta-transaction hash.
        let transaction_hash = InvokeTransactionV0 {
            max_fee: Fee(0),
            signature: signature.clone(),
            contract_address,
            entry_point_selector,
            calldata,
        }
        .calculate_transaction_hash(
            &self.context.tx_context.block_context.chain_info.chain_id,
            &signed_tx_version(&TransactionVersion::ZERO, &TransactionOptions { only_query }),
        )?;

        let class_hash = self.state.get_class_hash_at(contract_address)?;

        // Replace `tx_context`.
        let new_tx_info = TransactionInfo::Deprecated(DeprecatedTransactionInfo {
            common_fields: CommonAccountFields {
                transaction_hash,
                version: TransactionVersion::ZERO,
                signature,
                nonce: Nonce(0.into()),
                sender_address: contract_address,
                only_query,
            },
            max_fee: Fee(0),
        });
        self.context.tx_context = Arc::new(TransactionContext {
            block_context: old_tx_context.block_context.clone(),
            tx_info: new_tx_info,
        });

        // No error should be propagated until we restore the old `tx_context`.
        let result = self.execute_inner_call(entry_point, remaining_gas).map_err(|error| {
            SyscallExecutionError::from_self_or_revert(error.try_extract_revert().map_original(
                |error| {
                    // TODO(lior): Change to meta-tx specific error.
                    error.as_call_contract_execution_error(
                        class_hash,
                        contract_address,
                        entry_point_selector,
                    )
                },
            ))
        });

        // Restore the old `tx_context`.
        self.context.tx_context = old_tx_context;

        result
    }
```

**File:** crates/starknet_api/src/transaction_hash.rs (L317-334)
```rust
fn get_common_invoke_transaction_v0_hash(
    transaction: &InvokeTransactionV0,
    chain_id: &ChainId,
    is_deprecated: bool,
    transaction_version: &TransactionVersion,
) -> Result<TransactionHash, StarknetApiError> {
    Ok(TransactionHash(
        HashChain::new()
            .chain(&INVOKE)
            .chain_if_fn(|| if !is_deprecated { Some(transaction_version.0) } else { None })
            .chain(transaction.contract_address.0.key())
            .chain(&transaction.entry_point_selector.0)
            .chain(&HashChain::new().chain_iter(transaction.calldata.0.iter()).get_pedersen_hash())
            .chain_if_fn(|| if !is_deprecated { Some(transaction.max_fee.0.into()) } else { None })
            .chain(&Felt::try_from(chain_id)?)
            .get_pedersen_hash(),
    ))
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/transaction_hash/transaction_hash.cairo (L294-315)
```text
// Computes the hash of a v0 meta transaction. See the `meta_tx_v0` syscall.
func compute_meta_tx_v0_hash{pedersen_ptr: HashBuiltin*}(
    contract_address: felt,
    entry_point_selector: felt,
    calldata: felt*,
    calldata_size: felt,
    chain_id: felt,
) -> felt {
    let (tx_hash) = deprecated_get_transaction_hash{hash_ptr=pedersen_ptr}(
        tx_hash_prefix=INVOKE_HASH_PREFIX,
        version=0,
        contract_address=contract_address,
        entry_point_selector=entry_point_selector,
        calldata_size=calldata_size,
        calldata=calldata,
        max_fee=0,
        chain_id=chain_id,
        additional_data_size=0,
        additional_data=cast(0, felt*),
    );
    return tx_hash;
}
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/constants.cairo (L7-7)
```text
const INVOKE_HASH_PREFIX = 'invoke';
```

**File:** crates/apollo_starknet_os_program/src/cairo/starkware/starknet/core/os/execution/syscall_impls.cairo (L285-345)
```text
// Executes a v0 meta transaction. Specifically, calls another contract where:
// * The signature is replaced with the given signature.
// * The caller is the OS (address 0).
// * The transaction version is replaced by 0.
// * The transaction hash is replaced by the corresponding version-0 transaction hash.
// The changes apply to the called contract and the inner contracts it calls.
func execute_meta_tx_v0{
    range_check_ptr,
    syscall_ptr: felt*,
    builtin_ptrs: BuiltinPointers*,
    contract_state_changes: DictAccess*,
    contract_class_changes: DictAccess*,
    revert_log: RevertLogEntry*,
    outputs: OsCarriedOutputs*,
}(block_context: BlockContext*, caller_execution_context: ExecutionContext*) {
    alloc_locals;

    let request = cast(syscall_ptr + RequestHeader.SIZE, MetaTxV0Request*);
    local calldata_start: felt* = request.calldata_start;
    local calldata_size = request.calldata_end - calldata_start;

    let specific_base_gas_cost = (
        META_TX_V0_GAS_COST + META_TX_V0_CALLDATA_FACTOR_GAS_COST * calldata_size
    );
    let (success, remaining_gas) = reduce_syscall_base_gas(
        specific_base_gas_cost=specific_base_gas_cost, request_struct_size=MetaTxV0Request.SIZE
    );
    if (success == FALSE) {
        // Not enough gas to execute the syscall.
        return ();
    }

    local contract_address = request.contract_address;
    local selector = request.selector;
    local caller_execution_info: ExecutionInfo* = caller_execution_context.execution_info;
    local old_tx_info: TxInfo* = caller_execution_info.tx_info;

    if (selector != EXECUTE_ENTRY_POINT_SELECTOR) {
        write_failure_response(remaining_gas=remaining_gas, failure_felt=ERROR_INVALID_ARGUMENT);
        return ();
    }

    // Sanity check: Verify that `signature` is a valid Sierra array.
    assert_nn_le(request.signature_end - request.signature_start, SIERRA_ARRAY_LEN_BOUND - 1);

    let (state_entry: StateEntry*) = dict_read{dict_ptr=contract_state_changes}(
        key=contract_address
    );

    // Compute the meta-transaction hash.
    let pedersen_ptr = builtin_ptrs.selectable.pedersen;
    with pedersen_ptr {
        let meta_tx_hash = compute_meta_tx_v0_hash(
            contract_address=contract_address,
            entry_point_selector=selector,
            calldata=calldata_start,
            calldata_size=calldata_size,
            chain_id=old_tx_info.chain_id,
        );
    }
    update_pedersen_in_builtin_ptrs(pedersen_ptr=pedersen_ptr);
```
