### Title
Unchecked `SetAuthority` Instruction Lets a Program's Upgrade/Buffer Authority Be Transferred to an Unverified, Potentially Unspendable Pubkey - (File: programs/bpf_loader/src/lib.rs)

### Summary
The `bpf_loader_upgradeable` program's `UpgradeableLoaderInstruction::SetAuthority` handler transfers the upgrade authority (or buffer authority) of a program to any `new_authority` pubkey supplied in the instruction accounts, without requiring proof that anyone actually controls the corresponding private key. This mirrors the reported `Ownable.transferOwnership` weakness: the new "owner" address is accepted without any validity/liveness check, so a mistake in the transfer can permanently lock the protocol (here, a Solana program) out of its privileged functions (upgrades, authority changes, finalization).

### Finding Description
In the unchecked branch of `SetAuthority`, the new authority is read only as an account key (not required to sign): [1](#0-0) 

For both the `Buffer` and `ProgramData` cases, `new_authority` is taken via `get_key_of_instruction_account(2).ok()` and written directly into account state with `account.set_state(...)`, with **no signature requirement on the new authority account**: [2](#0-1) 

Contrast this with the safer `SetAuthorityChecked` variant, added specifically to close this gap, which explicitly requires `is_instruction_account_signer(2)` to prove key ownership of the new authority before committing the state change: [3](#0-2) 

Because `SetAuthority` (unchecked) is still a fully supported, unprivileged instruction path reachable directly by any ordinary client via `solana_loader_v3_interface::instruction::set_upgrade_authority` / `set_buffer_authority`, and the Agave CLI itself still exposes it by default: [4](#0-3) 

a client that fabricates, mistypes, or otherwise supplies a `new_upgrade_authority`/`new_buffer_authority` pubkey it does not control (or for which the private key is lost/unknown) can commit that authority into `ProgramData`/`Buffer` state permanently. The CLI's own help text acknowledges the danger ("It is strongly recommended to pass in a keypair to prevent mistakes in setting the upgrade authority... Alternatively... `--final`"), confirming that the unchecked instruction has no on-chain safeguard and relies entirely on client-side care: [5](#0-4) 

Once the authority is set to an unreachable pubkey, all future `Upgrade`, `SetAuthority`, `SetAuthorityChecked`, `Close`, and `ExtendProgram` operations that require the current authority's signature (`IncorrectAuthority`/`MissingRequiredSignature` checks) become permanently unsatisfiable, functionally equivalent to `Immutable` — the program is bricked with no path to recovery, exactly like the reported `Ownable` scenario where the whole protocol is "locked out of its permissioned functionalities."

### Impact Explanation
A locked-out upgrade/buffer authority permanently disables the ability to upgrade, finalize, or otherwise administer the affected program. For upgradeable programs holding significant value or logic (e.g., DeFi protocols deployed on Solana), this is equivalent to a denial-of-service on protocol governance/maintenance — no further authority changes or upgrades are possible, and any embedded bug becomes unfixable. This does not cause direct lamport theft or cluster-wide consensus divergence, but it is a severe, irreversible loss of control over program administration, matching the "protocol locked out of its important functions" impact described in the reference report.

### Likelihood Explanation
Likelihood is low, consistent with the original report's score (Impact 5 / Likelihood 1): triggering this requires the *current, legitimate* authority holder to make a mistake (typo, wrong keypair, copy-paste error) when calling the unchecked `SetAuthority` instruction rather than `SetAuthorityChecked`. There is no attacker-controlled exploitation path from a third party; it is purely an operational/self-inflicted risk enabled by the missing on-chain validation, which is why Agave already added the checked variant as a mitigation while leaving the unchecked instruction available for backward compatibility.

### Recommendation
Since `SetAuthorityChecked` (requiring the new authority to co-sign, proving key possession) already exists as the safe alternative, the risk-acceptance path mirrors the original report's "RISK ACCEPTED" resolution: continue documenting/steering all authority-transfer flows (CLI and SDK helpers) toward `SetAuthorityChecked`, and consider deprecating or gating the unchecked `SetAuthority` behind an explicit opt-in flag, since it is the direct on-chain analog of the unchecked `transferOwnership` pattern flagged in the report.

### Proof of Concept
1. Deploy an upgradeable program via `bpf_loader_upgradeable`, with `upgrade_authority_address = A`.
2. Authority `A` calls `set_upgrade_authority(program_id, A, Some(B))` (the unchecked instruction) where `B` is a pubkey `A` does not actually control the private key for (e.g., a typo'd address or an address generated without saving the keypair) — see instruction construction and CLI flow at: [6](#0-5) 
3. The `SetAuthority` handler accepts `B` as `new_authority` and commits it to `ProgramData.upgrade_authority_address` without requiring `B` to sign, per: [7](#0-6) 
4. Any subsequent `Upgrade`/`SetAuthority`/`Close` call now requires a valid signature from `B`, which can never be produced, permanently bricking the program's administrative capabilities.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L549-576)
```rust
        UpgradeableLoaderInstruction::SetAuthority => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let mut account = instruction_context.try_borrow_instruction_account(0)?;
            let present_authority_key = instruction_context.get_key_of_instruction_account(1)?;
            let new_authority = instruction_context.get_key_of_instruction_account(2).ok();

            match account.get_state()? {
                UpgradeableLoaderState::Buffer { authority_address } => {
                    if new_authority.is_none() {
                        ic_logger_msg!(log_collector, "Buffer authority is not optional");
                        return Err(InstructionError::IncorrectAuthority);
                    }
                    if authority_address.is_none() {
                        ic_logger_msg!(log_collector, "Buffer is immutable");
                        return Err(InstructionError::Immutable);
                    }
                    if authority_address != Some(*present_authority_key) {
                        ic_logger_msg!(log_collector, "Incorrect buffer authority provided");
                        return Err(InstructionError::IncorrectAuthority);
                    }
                    if !instruction_context.is_instruction_account_signer(1)? {
                        ic_logger_msg!(log_collector, "Buffer authority did not sign");
                        return Err(InstructionError::MissingRequiredSignature);
                    }
                    account.set_state(&UpgradeableLoaderState::Buffer {
                        authority_address: new_authority.cloned(),
                    })?;
                }
```

**File:** programs/bpf_loader/src/lib.rs (L577-617)
```rust
                UpgradeableLoaderState::ProgramData {
                    slot,
                    upgrade_authority_address,
                } => {
                    if upgrade_authority_address.is_none() {
                        ic_logger_msg!(log_collector, "Program not upgradeable");
                        return Err(InstructionError::Immutable);
                    }
                    if upgrade_authority_address != Some(*present_authority_key) {
                        ic_logger_msg!(log_collector, "Incorrect upgrade authority provided");
                        return Err(InstructionError::IncorrectAuthority);
                    }
                    if !instruction_context.is_instruction_account_signer(1)? {
                        ic_logger_msg!(log_collector, "Upgrade authority did not sign");
                        return Err(InstructionError::MissingRequiredSignature);
                    }
                    if invoke_context
                        .get_feature_set()
                        .disable_sbpf_v0_v1_v2_deployment
                        && new_authority.is_none()
                        && let Some(program) = account
                            .get_data()
                            .get(UpgradeableLoaderState::size_of_programdata_metadata()..)
                        && let Ok(sbpf_version) = get_sbpf_version(program)
                        && sbpf_version < SBPFVersion::V3
                    {
                        return Err(InstructionError::InvalidAccountData);
                    }
                    account.set_state(&UpgradeableLoaderState::ProgramData {
                        slot,
                        upgrade_authority_address: new_authority.cloned(),
                    })?;
                }
                _ => {
                    ic_logger_msg!(log_collector, "Account does not support authorities");
                    return Err(InstructionError::InvalidArgument);
                }
            }

            ic_logger_msg!(log_collector, "New authority {:?}", new_authority);
        }
```

**File:** programs/bpf_loader/src/lib.rs (L618-684)
```rust
        UpgradeableLoaderInstruction::SetAuthorityChecked => {
            if !invoke_context
                .get_feature_set()
                .enable_bpf_loader_set_authority_checked_ix
            {
                return Err(InstructionError::InvalidInstructionData);
            }

            instruction_context.check_number_of_instruction_accounts(3)?;
            let mut account = instruction_context.try_borrow_instruction_account(0)?;
            let present_authority_key = instruction_context.get_key_of_instruction_account(1)?;
            let new_authority_key = instruction_context.get_key_of_instruction_account(2)?;

            match account.get_state()? {
                UpgradeableLoaderState::Buffer { authority_address } => {
                    if authority_address.is_none() {
                        ic_logger_msg!(log_collector, "Buffer is immutable");
                        return Err(InstructionError::Immutable);
                    }
                    if authority_address != Some(*present_authority_key) {
                        ic_logger_msg!(log_collector, "Incorrect buffer authority provided");
                        return Err(InstructionError::IncorrectAuthority);
                    }
                    if !instruction_context.is_instruction_account_signer(1)? {
                        ic_logger_msg!(log_collector, "Buffer authority did not sign");
                        return Err(InstructionError::MissingRequiredSignature);
                    }
                    if !instruction_context.is_instruction_account_signer(2)? {
                        ic_logger_msg!(log_collector, "New authority did not sign");
                        return Err(InstructionError::MissingRequiredSignature);
                    }
                    account.set_state(&UpgradeableLoaderState::Buffer {
                        authority_address: Some(*new_authority_key),
                    })?;
                }
                UpgradeableLoaderState::ProgramData {
                    slot,
                    upgrade_authority_address,
                } => {
                    if upgrade_authority_address.is_none() {
                        ic_logger_msg!(log_collector, "Program not upgradeable");
                        return Err(InstructionError::Immutable);
                    }
                    if upgrade_authority_address != Some(*present_authority_key) {
                        ic_logger_msg!(log_collector, "Incorrect upgrade authority provided");
                        return Err(InstructionError::IncorrectAuthority);
                    }
                    if !instruction_context.is_instruction_account_signer(1)? {
                        ic_logger_msg!(log_collector, "Upgrade authority did not sign");
                        return Err(InstructionError::MissingRequiredSignature);
                    }
                    if !instruction_context.is_instruction_account_signer(2)? {
                        ic_logger_msg!(log_collector, "New authority did not sign");
                        return Err(InstructionError::MissingRequiredSignature);
                    }
                    account.set_state(&UpgradeableLoaderState::ProgramData {
                        slot,
                        upgrade_authority_address: Some(*new_authority_key),
                    })?;
                }
                _ => {
                    ic_logger_msg!(log_collector, "Account does not support authorities");
                    return Err(InstructionError::InvalidArgument);
                }
            }

            ic_logger_msg!(log_collector, "New authority {:?}", new_authority_key);
```

**File:** cli/src/program.rs (L487-503)
```rust
                        .arg(
                            Arg::with_name("new_upgrade_authority")
                                .long("new-upgrade-authority")
                                .value_name("NEW_UPGRADE_AUTHORITY")
                                .required_unless("final")
                                .takes_value(true)
                                .help(
                                    "New upgrade authority (keypair or pubkey). It is strongly \
                                     recommended to pass in a keypair to prevent mistakes in \
                                     setting the upgrade authority. You can opt out of this \
                                     behavior by passing \
                                     --skip-new-upgrade-authority-signer-check if you are really \
                                     confident that you are setting the correct authority. \
                                     Alternatively, If you wish to make the program immutable, \
                                     you should ignore this arg and pass the --final flag.",
                                ),
                        )
```

**File:** cli/src/program.rs (L1763-1809)
```rust
async fn process_set_authority(
    rpc_client: &RpcClient,
    config: &CliConfig<'_>,
    program_pubkey: Option<Pubkey>,
    buffer_pubkey: Option<Pubkey>,
    authority: Option<SignerIndex>,
    new_authority: Option<Pubkey>,
    sign_only: bool,
    dump_transaction_message: bool,
    blockhash_query: &BlockhashQuery,
) -> ProcessResult {
    let authority_signer = if let Some(index) = authority {
        config.signers[index]
    } else {
        return Err("Set authority requires the current authority".into());
    };

    trace!("Set a new authority");
    let blockhash = blockhash_query
        .get_blockhash(rpc_client, config.commitment)
        .await?;

    let mut tx = if let Some(ref pubkey) = program_pubkey {
        Transaction::new_unsigned(Message::new(
            &[loader_v3_instruction::set_upgrade_authority(
                pubkey,
                &authority_signer.pubkey(),
                new_authority.as_ref(),
            )],
            Some(&config.signers[0].pubkey()),
        ))
    } else if let Some(pubkey) = buffer_pubkey {
        if let Some(ref new_authority) = new_authority {
            Transaction::new_unsigned(Message::new(
                &[loader_v3_instruction::set_buffer_authority(
                    &pubkey,
                    &authority_signer.pubkey(),
                    new_authority,
                )],
                Some(&config.signers[0].pubkey()),
            ))
        } else {
            return Err("Buffer authority cannot be None".into());
        }
    } else {
        return Err("Program or Buffer not provided".into());
    };
```
