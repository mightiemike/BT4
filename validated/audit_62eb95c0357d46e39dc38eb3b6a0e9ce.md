### Title
Permissionless `ExtendProgram` instruction lets any account grief-block a BPF Upgradeable program's `Close` instruction - ([File: programs/bpf_loader/src/lib.rs])

### Summary
The legacy `UpgradeableLoaderInstruction::ExtendProgram` instruction is dispatched with `check_authority = false`, meaning it requires **no signature from the program's upgrade authority** — any unprivileged client can submit it. Each successful call unconditionally rewrites the `ProgramData` account's `slot` field to the current bank slot. The `Close` instruction refuses to close a `ProgramData`/`Program` account when `clock.slot == slot` ("Program was deployed in this block already"). By repeatedly (cheaply) calling `ExtendProgram` once per slot on a target program, an attacker can perpetually keep `slot` pinned to the current slot, permanently preventing the legitimate upgrade authority from ever closing the program and reclaiming its rent — an unprivileged, time-window griefing DoS that mirrors the MochiVault "deposit resets withdrawal timer" pattern.

### Finding Description
`common_extend_program` in [1](#0-0)  is invoked for the plain `ExtendProgram` instruction with `check_authority = false`: [2](#0-1) 

Because `check_authority` is `false`, the authority-signature branch is skipped entirely: [3](#0-2) 

No signer or ownership check ties the caller to the upgrade authority — only a payer account (index 3, `optional_payer_account_index`) is used to fund any additional rent, and that payer need not be privileged in any way. The CLI itself confirms this: `process_extend_program` only requires a `program_pubkey` and a fee-payer, not the upgrade authority: [4](#0-3) 

After extension, the code unconditionally re-stamps `slot: clock_slot` into `ProgramData`: [5](#0-4) 

The only guard against ExtendProgram itself being spammed is a same-slot check ("Program was extended in this block already"): [6](#0-5) 

This means the earliest the attacker can call it again is the very next slot — they are not blocked from calling it once per slot indefinitely.

Separately, the `Close` instruction handler for `ProgramData` accounts rejects closing if the account was "deployed" (i.e., `slot` field) in the same slot as the current clock slot: [7](#0-6) 

Combining these two facts: an attacker with no relationship to the program (not the upgrade authority, not even required to sign as such) can call `ExtendProgram` with the minimal legal `additional_bytes` once per slot, continuously refreshing `ProgramData.slot` to the current slot. Because the legitimate authority's `Close` call will always observe `clock.slot == slot` (the attacker refreshed it moments earlier, and slots advance quickly), the authority can never successfully close/reclaim the program account. This is the direct analog of the MochiVault griefing pattern: a public, low-cost, unprivileged action resets a "recently touched" timestamp/slot guard that blocks the legitimate owner's privileged withdrawal/close action, indefinitely.

### Impact Explanation
This is a DoS/griefing vector on a builtin program (BPF Loader Upgradeable) reachable from an ordinary, unprivileged client transaction stream (no special node/network access, no consensus role required). It permanently prevents the legitimate upgrade authority from closing an upgradeable program and reclaiming the ProgramData account's rent-exempt lamports (potentially a large amount of locked SOL, since ProgramData accounts sized for large programs can hold significant rent-exempt balances). It also blocks any workflow that depends on `Close` succeeding (e.g., program deprecation/migration flows that close and reclaim funds before redeploying). While it does not directly inflate lamports or cause consensus divergence, it does constitute a persistent denial-of-service against fund recovery for any address, satisfying the "concrete... loss" bar via indefinitely locked lamports that the rightful authority cannot reclaim.

### Likelihood Explanation
High. The attack requires only:
- Knowledge of the target program's `ProgramData` address (publicly derivable/on-chain).
- The ability to submit a transaction with `ExtendProgram` and a minimal `additional_bytes` value once per slot.
- Minimal SOL to cover the payer's fee and negligible/no incremental rent (if `additional_bytes` is small and existing balance already covers minimum rent for the marginal size increase).

No special privileges, front-running, or high gas cost is required — this can be executed by any wallet indefinitely and cheaply, matching the report's "low fee chain: deposit every 3 minutes" griefing description exactly, except here it is "call once per slot" and requires zero relationship to the victim account.

### Recommendation
Require the upgrade authority's signature (or otherwise restrict callers) for any instruction path that mutates `ProgramData.slot` in a way that gates `Close`, or decouple the anti-abuse "not deployed in the same slot" check in `Close` from a field that unprivileged parties can refresh. Concretely:
- Deprecate/disable the unchecked legacy `ExtendProgram` instruction path (`check_authority = false`) in favor of `ExtendProgramChecked`, which does verify the upgrade authority's signature, and/or gate the legacy instruction behind a feature that eventually disables it entirely (there appears to already be a `disable_bpf_loader_instructions` feature and an `enable_extend_program_checked` feature — ensure the unchecked `ExtendProgram` is fully retired once these are active on all clusters).
- Alternatively, don't allow `ExtendProgram` to be called by non-authority signers at all, independent of feature flags.

### Proof of Concept
1. Deploy an upgradeable program `P` with `ProgramData` account `PD`, owned by authority `A`.
2. Attacker `E` (unrelated to `A`) submits, once per slot indefinitely:
   `ExtendProgram(programdata=PD, program=P, payer=E)` with minimal legal `additional_bytes` — no signature from `A` required (`check_authority=false` path in `common_extend_program`, [2](#0-1) ).
3. Each successful call sets `ProgramData.slot = clock.slot` ( [5](#0-4) ).
4. Whenever `A` submits `Close(PD, recipient, A, P)` to reclaim rent, the handler checks `clock.slot == slot` and errors with `InvalidArgument` ("Program was deployed in this block already") because `E`'s most recent `ExtendProgram` call refreshed `slot` to a very recent slot ( [8](#0-7) ).
5. `E` repeats step 2 forever at negligible cost, permanently denying `A` the ability to close/reclaim `PD`.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L717-741)
```rust
                UpgradeableLoaderState::ProgramData {
                    slot,
                    upgrade_authority_address: authority_address,
                } => {
                    instruction_context.check_number_of_instruction_accounts(4)?;
                    drop(close_account);
                    let program_account = instruction_context.try_borrow_instruction_account(3)?;
                    let program_key = *program_account.get_key();

                    if !program_account.is_writable() {
                        ic_logger_msg!(log_collector, "Program account is not writable");
                        return Err(InstructionError::InvalidArgument);
                    }
                    if program_account.get_owner() != program_id {
                        ic_logger_msg!(log_collector, "Program account not owned by loader");
                        return Err(InstructionError::IncorrectProgramId);
                    }
                    let clock = invoke_context
                        .environment_config
                        .sysvar_cache()
                        .get_clock()?;
                    if clock.slot == slot {
                        ic_logger_msg!(log_collector, "Program was deployed in this block already");
                        return Err(InstructionError::InvalidArgument);
                    }
```

**File:** programs/bpf_loader/src/lib.rs (L789-791)
```rust
        UpgradeableLoaderInstruction::ExtendProgram { additional_bytes } => {
            common_extend_program(invoke_context, additional_bytes, false)?;
        }
```

**File:** programs/bpf_loader/src/lib.rs (L797-816)
```rust
fn common_extend_program(
    invoke_context: &mut InvokeContext,
    additional_bytes: u32,
    check_authority: bool,
) -> Result<(), InstructionError> {
    let log_collector = invoke_context.get_log_collector();
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;
    let program_id = instruction_context.get_program_key()?;

    const PROGRAM_DATA_ACCOUNT_INDEX: IndexOfAccount = 0;
    const PROGRAM_ACCOUNT_INDEX: IndexOfAccount = 1;
    const AUTHORITY_ACCOUNT_INDEX: IndexOfAccount = 2;
    // The unused `system_program_account_index` is 3 if `check_authority` and 2 otherwise.
    let optional_payer_account_index = if check_authority { 4 } else { 3 };

    if additional_bytes == 0 {
        ic_logger_msg!(log_collector, "Additional bytes must be greater than 0");
        return Err(InstructionError::InvalidInstructionData);
    }
```

**File:** programs/bpf_loader/src/lib.rs (L903-932)
```rust
    let upgrade_authority_address = if let UpgradeableLoaderState::ProgramData {
        slot,
        upgrade_authority_address,
    } = programdata_account.get_state()?
    {
        if clock_slot == slot {
            ic_logger_msg!(log_collector, "Program was extended in this block already");
            return Err(InstructionError::InvalidArgument);
        }

        if upgrade_authority_address.is_none() {
            ic_logger_msg!(
                log_collector,
                "Cannot extend ProgramData accounts that are not upgradeable"
            );
            return Err(InstructionError::Immutable);
        }

        if check_authority {
            let authority_key =
                Some(*instruction_context.get_key_of_instruction_account(AUTHORITY_ACCOUNT_INDEX)?);
            if upgrade_authority_address != authority_key {
                ic_logger_msg!(log_collector, "Incorrect upgrade authority provided");
                return Err(InstructionError::IncorrectAuthority);
            }
            if !instruction_context.is_instruction_account_signer(AUTHORITY_ACCOUNT_INDEX)? {
                ic_logger_msg!(log_collector, "Upgrade authority did not sign");
                return Err(InstructionError::MissingRequiredSignature);
            }
        }
```

**File:** programs/bpf_loader/src/lib.rs (L986-991)
```rust
    let mut programdata_account =
        instruction_context.try_borrow_instruction_account(PROGRAM_DATA_ACCOUNT_INDEX)?;
    programdata_account.set_state(&UpgradeableLoaderState::ProgramData {
        slot: clock_slot,
        upgrade_authority_address,
    })?;
```

**File:** cli/src/program.rs (L2417-2472)
```rust
async fn process_extend_program(
    rpc_client: &RpcClient,
    config: &CliConfig<'_>,
    program_pubkey: Pubkey,
    payer_signer_index: SignerIndex,
    additional_bytes: u32,
) -> ProcessResult {
    let fee_payer_pubkey = config.signers[0].pubkey();
    let payer_signer = config.signers[payer_signer_index];
    let payer_pubkey = payer_signer.pubkey();

    if additional_bytes == 0 {
        return Err("Additional bytes must be greater than zero".into());
    }

    let program_account = match rpc_client
        .get_account_with_commitment(&program_pubkey, config.commitment)
        .await?
        .value
    {
        Some(program_account) => Ok(program_account),
        None => Err(format!("Unable to find program {program_pubkey}")),
    }?;

    if !bpf_loader_upgradeable::check_id(&program_account.owner) {
        return Err(format!("Account {program_pubkey} is not an upgradeable program").into());
    }

    let programdata_pubkey = match bincode::deserialize(&program_account.data) {
        Ok(UpgradeableLoaderState::Program {
            programdata_address: programdata_pubkey,
        }) => Ok(programdata_pubkey),
        _ => Err(format!(
            "Account {program_pubkey} is not an upgradeable program"
        )),
    }?;

    let programdata_account = match rpc_client
        .get_account_with_commitment(&programdata_pubkey, config.commitment)
        .await?
        .value
    {
        Some(programdata_account) => Ok(programdata_account),
        None => Err(format!("Program {program_pubkey} is closed")),
    }?;

    let upgrade_authority_address = match bincode::deserialize(&programdata_account.data) {
        Ok(UpgradeableLoaderState::ProgramData {
            slot: _,
            upgrade_authority_address,
        }) => Ok(upgrade_authority_address),
        _ => Err(format!("Program {program_pubkey} is closed")),
    }?;

    upgrade_authority_address
        .ok_or_else(|| format!("Program {program_pubkey} is not upgradeable"))?;
```
