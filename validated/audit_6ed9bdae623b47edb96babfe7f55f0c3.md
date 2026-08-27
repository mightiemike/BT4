### Title
Missing `check_account_info_pointer` validation for `AccountInfo.owner` in CPI Rust ABI enables cross-account owner-pointer aliasing - (File: `program-runtime/src/cpi.rs`, function `CallerAccount::from_account_info`)

### Summary
In `CallerAccount::from_account_info` (Rust CPI ABI), the `lamports` and `data` pointers embedded in an attacker-supplied `AccountInfo` are validated against the runtime's own recorded serialized addresses via `check_account_info_pointer`, but the `owner` pointer is not. This asymmetry (the C ABI counterpart `from_sol_account_info` *does* validate `owner`) means a malicious SBF program can set `AccountInfo.owner` to an arbitrary VM address before invoking CPI, causing `translate_type_mut_for_cpi::<Pubkey>` to hand back a mutable reference into memory outside that account's own serialized slot.

### Finding Description
`CallerAccount::from_account_info` at [1](#0-0)  shows that `lamports` is checked with `check_account_info_pointer(invoke_context, *ptr, account_metadata.vm_lamports_addr, "lamports")` before translation, and `data` is likewise checked at [2](#0-1) . The `owner` field, however, is translated directly from `account_info.owner as *const _ as u64` with **no** call to `check_account_info_pointer` against `account_metadata.vm_owner_addr`: [3](#0-2) 

By contrast, the C ABI path `from_sol_account_info` performs this exact validation for `owner_addr` against `account_metadata.vm_owner_addr`: [4](#0-3) 

Since the `AccountInfo` array passed to a CPI syscall is fully attacker-controlled bytes translated out of VM memory (`translate_account_infos`, `translate_accounts_common` at [5](#0-4) ), the numeric value of `account_info.owner` is whatever the SBF program placed there — it need not point at the account's own serialized `owner` slot inside the per-instruction parameter buffer. `translate_type_mut_for_cpi` only checks that the target address is a valid, aligned, *writable* memory region — it performs no ownership/identity check that the address actually belongs to the account being processed [6](#0-5) .

After the callee instruction executes, `update_caller_account` writes the callee's real (possibly changed) owner back through this unchecked pointer: [7](#0-6) 

If an attacker crafts `AccountInfo.owner` to alias the serialized `owner` field slot of a *different* account passed in the same instruction's account list (all accounts share one contiguous serialized parameter buffer, with each account's fields at fixed offsets, as illustrated by the layout construction in [8](#0-7) ), the write intended for the "own" account's owner instead lands in the victim account's serialized owner bytes. Those bytes are later copied back into the real account state at instruction/CPI exit, producing an owner corruption/transfer that was never authorized for the victim account.

### Impact Explanation
If exploitable end-to-end, this is a cross-account privilege-escalation / unauthorized ownership-transfer primitive (Solana bounty category: "cross-account or CPI privilege escalation" / "theft of funds without the owner's signature" if the victim account subsequently becomes controllable by an attacker-owned program). The corruption occurs as a side effect of an otherwise "legitimate" CPI account_info construction, matching the "privilege exactness" invariant violation described in the prompt.

### Likelihood Explanation
Preconditions are fully within reach of an ordinary unprivileged attacker: they need only deploy their own SBF program that builds a crafted `AccountInfo` array (fabricated in program-owned memory or with a manipulated `owner` pointer field) and invoke CPI with `syscall_parameter_address_restrictions` active but relying on the fact that this specific check is not applied to `owner` in the Rust ABI path. No validator, operator, or privileged access is required — this is reachable purely from transaction/instruction data and attacker-supplied program bytecode.

However, whether the corruption actually persists into consensus state depends on downstream logic not fully traced here — specifically whether the top-level `deserialize_parameters`/account-commit step re-validates that an account's owner may only be changed by its true current owner program before copying serialized bytes back into `TransactionContext`, and whether the underlying `MemoryMapping`/`MemoryRegion` layout for the two accounts' serialized slots are contiguous/writable such that the aliasing write actually type-checks as valid `Store` access rather than triggering an access violation. I was not able to fully confirm these downstream commit-time authorization checks or the precise memory-region boundary enforcement within the available context, so I cannot certify with full confidence that this leads to an actual on-chain owner corruption versus a `EbpfError::AccessViolation`/graceful transaction failure.

### Recommendation
Add the missing `check_account_info_pointer(invoke_context, account_info.owner as *const _ as u64, account_metadata.vm_owner_addr, "owner")` call in `CallerAccount::from_account_info`, mirroring the check already present in `from_sol_account_info`, gated the same way behind `syscall_parameter_address_restrictions`.

### Proof of Concept
Unit/integration test plan (SVM/BankClient level, in `programs/sbf/tests/programs.rs` alongside the existing `test_mem_syscalls_overlap_account_begin_or_end`):
1. Deploy two SBF test programs: an "attacker" caller program and a benign "callee" program (e.g. reuse `solana_sbf_rust_invoke`).
2. In the caller program, construct an `AccountInfo` array for CPI where `account_info[0].owner` is set to point at the byte offset corresponding to `account_info[1]`'s serialized owner field within `MM_INPUT_START` region (compute via the same offset arithmetic as `SerializedAccountMetadata::vm_owner_addr`).
3. Invoke the callee via `invoke()`, letting the callee set/leave its own owner (e.g. unchanged, or reassign to a program the attacker controls if the callee allows it).
4. After CPI returns, assert (from the bank/test harness) that `account_info[1]`'s actual on-chain `owner` (the victim) has NOT changed, i.e. it must equal its owner as declared before the CPI.
5. Add a helper that iterates all `SerializedAccountMetadata.vm_owner_addr`/`vm_lamports_addr`/`vm_data_addr`/`vm_key_addr` ranges for every account in the current instruction and asserts pairwise disjointness before CPI executes, failing the test if any `AccountInfo.owner` pointer resolves outside its own account's designated address range.
Expected (fixed) behavior: the CPI call fails with `CpiError`/`InstructionError::InvalidPointer` due to the new `check_account_info_pointer` check, rather than silently succeeding and corrupting the victim account's owner.

### Citations

**File:** program-runtime/src/cpi.rs (L308-334)
```rust
        let lamports = {
            // Double translate lamports out of RefCell
            let ptr = translate_type::<u64>(
                memory_mapping,
                account_info.lamports.as_ptr() as u64,
                check_aligned,
            )?;
            if syscall_parameter_address_restrictions {
                if account_info.lamports.as_ptr() as u64 >= solana_sbpf::ebpf::MM_INPUT_START {
                    return Err(Box::new(CpiError::InvalidPointer));
                }

                check_account_info_pointer(
                    invoke_context,
                    *ptr,
                    account_metadata.vm_lamports_addr,
                    "lamports",
                )?;
            }
            translate_type_mut_for_cpi::<u64>(memory_mapping, *ptr, check_aligned)?
        };

        let owner = translate_type_mut_for_cpi::<Pubkey>(
            memory_mapping,
            account_info.owner as *const _ as u64,
            check_aligned,
        )?;
```

**File:** program-runtime/src/cpi.rs (L349-355)
```rust
            if syscall_parameter_address_restrictions {
                check_account_info_pointer(
                    invoke_context,
                    data.as_ptr() as u64,
                    account_metadata.vm_data_addr,
                    "data",
                )?;
```

**File:** program-runtime/src/cpi.rs (L435-440)
```rust
            check_account_info_pointer(
                invoke_context,
                account_info.owner_addr,
                account_metadata.vm_owner_addr,
                "owner",
            )?;
```

**File:** program-runtime/src/cpi.rs (L901-952)
```rust
fn translate_account_infos<T, R>(
    account_infos_addr: u64,
    account_infos_len: u64,
    key_addr: impl Fn(&T) -> u64,
    invoke_context: &InvokeContext,
    memory_mapping: &MemoryMapping,
    check_aligned: bool,
    cb: impl FnOnce(&[T], Vec<&Pubkey>) -> R,
) -> Result<R, Error> {
    let syscall_parameter_address_restrictions = invoke_context
        .get_feature_set()
        .syscall_parameter_address_restrictions;

    // In the same vein as the other check_account_info_pointer() checks, we don't lock
    // this pointer to a specific address but we don't want it to be inside accounts, or
    // callees might be able to write to the pointed memory.
    if syscall_parameter_address_restrictions
        && account_infos_addr
            .saturating_add(account_infos_len.saturating_mul(std::mem::size_of::<T>() as u64))
            >= ebpf::MM_INPUT_START
    {
        return Err(CpiError::InvalidPointer.into());
    }

    let account_infos = translate_slice::<T>(
        memory_mapping,
        account_infos_addr,
        account_infos_len,
        check_aligned,
    )?;
    check_account_infos(account_infos.len())?;

    let account_infos_bytes = account_infos.len().saturating_mul(ACCOUNT_INFO_BYTE_SIZE);

    let amount = (account_infos_bytes as u64)
        .checked_div(invoke_context.get_execution_cost().cpi_bytes_per_unit)
        .unwrap_or(u64::MAX);
    invoke_context.compute_meter.consume_checked(amount)?;

    let mut account_info_keys = Vec::with_capacity(account_infos_len as usize);
    #[expect(clippy::needless_range_loop)]
    for account_index in 0..account_infos_len as usize {
        #[expect(clippy::indexing_slicing)]
        let account_info = &account_infos[account_index];
        account_info_keys.push(translate_type::<Pubkey>(
            memory_mapping,
            key_addr(account_info),
            check_aligned,
        )?);
    }
    Ok(cb(account_infos, account_info_keys))
}
```

**File:** program-runtime/src/cpi.rs (L1244-1245)
```rust
    *caller_account.lamports = callee_account.get_lamports();
    *caller_account.owner = *callee_account.get_owner();
```

**File:** program-runtime/src/memory.rs (L158-164)
```rust
pub fn translate_type_mut_for_cpi<'a, T>(
    memory_mapping: &MemoryMapping,
    vm_addr: u64,
    check_aligned: bool,
) -> Result<&'a mut T, Box<dyn std::error::Error>> {
    translate_type_inner!(memory_mapping, AccessType::Store, vm_addr, T, check_aligned)
}
```

**File:** program-runtime/src/serialization.rs (L1545-1550)
```rust
        let account_start_offsets = [
            MM_INPUT_START,
            MM_INPUT_START + 4 + MAX_PERMITTED_DATA_INCREASE as u64,
            MM_INPUT_START + (4 + MAX_PERMITTED_DATA_INCREASE as u64) * 2,
            MM_INPUT_START + (4 + MAX_PERMITTED_DATA_INCREASE as u64) * 3,
        ];
```
