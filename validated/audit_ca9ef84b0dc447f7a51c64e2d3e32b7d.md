### Title
Griefing via pre-funding the derived ProgramData address blocks `bpf_loader_upgradeable::DeployWithMaxDataLen` deployments - (File: programs/bpf_loader/src/lib.rs)

### Summary
The `UpgradeableLoaderInstruction::DeployWithMaxDataLen` handler derives the ProgramData address deterministically from the new program's pubkey via `Pubkey::find_program_address(&[new_program_id.as_ref()], program_id)` and then creates that account using the classic `system_instruction::create_account`, which unconditionally fails if the destination account already has lamports. Because System `Transfer` only requires the sender to sign — the destination address needs no signature — any unprivileged client can pre-fund the publicly-derivable ProgramData PDA before the legitimate deployer's transaction lands, causing the deploy to fail with `AccountAlreadyInUse`. This is a direct analog of the FraxLendPairDeployer front-running/hash-collision bug: an unauthenticated actor blocks a specific, predictable deployment target chosen by an authenticated actor.

### Finding Description
`DeployWithMaxDataLen` computes the ProgramData PDA and validates it matches the account supplied by the caller: [1](#0-0) 

It then invokes the System Program's `create_account` (not the prefund-tolerant variant) to create that account: [2](#0-1) 

The System Program's `create_account` path rejects creation if the destination already carries lamports: [3](#0-2) 

Because a plain `Transfer` instruction only requires the sender to sign (the receiver does not need to sign or exist beforehand): [4](#0-3) 

any client can compute the same PDA (`find_program_address([program_id], bpf_loader_upgradeable::id())`) once they observe an unconfirmed `Deploy`/`DeployWithMaxDataLen` transaction (or even preemptively, if they know/guess the intended program keypair), and transfer 1 lamport to it before the deploy transaction lands, causing the CPI'd `create_account` to fail with `SystemError::AccountAlreadyInUse`, aborting the whole deployment instruction.

Notably, Agave has already recognized and partially remediated this exact class of griefing elsewhere in the System Program: a new `SystemInstruction::CreateAccountAllowPrefund` variant was added specifically to tolerate pre-funded destination accounts: [5](#0-4) [6](#0-5) 

The CLI's program-deploy path also has explicit handling/tests around "account with excess balance" blocking legitimate deploys, confirming this failure mode is a known, previously-encountered operational hazard: [7](#0-6) 

However, the `bpf_loader_upgradeable::DeployWithMaxDataLen` native-invoke at line 296 in `programs/bpf_loader/src/lib.rs` still uses the classic `create_account` CPI rather than the prefund-tolerant variant, so the underlying deploy path in the loader itself remains susceptible to this griefing even though the System Program has since added a mechanism to avoid it.

### Impact Explanation
This is a griefing / denial-of-service vector against program deployments and upgrades-from-scratch (initial `DeployWithMaxDataLen`, which is invoked for both `Program::Deploy` and the CLI's default deploy path when creating a brand-new upgradeable program). An attacker who front-runs (or preemptively funds) a target program's ProgramData PDA with even 1 lamport can repeatedly force `AccountAlreadyInUse` failures, forcing the deployer to either pay wasted fees for failed transactions or to choose a new program keypair — the exact "block deployment / force parameter change" outcome described in the FraxLend report. It does not cause fund loss, double-spend, or consensus divergence by itself, but it is a concrete, unprivileged-sender-reachable denial-of-service against a builtin program's account-creation flow.

### Likelihood Explanation
Likelihood is moderate-to-high in adversarial/competitive deployment scenarios: the ProgramData address is fully deterministic and publicly computable from the program's pubkey (`find_program_address([program_pubkey], bpf_loader_upgradeable::id())`), and blocking it costs only a single lamport transfer plus a transaction fee. The main precondition is that the attacker must know (or front-run/observe) the target `new_program_id` before the deploy transaction is confirmed, which is trivially true for mempool-visible or CLI-announced deployments, and can also be launched proactively/speculatively against commonly-reused deterministic program keypairs (e.g. `get_default_program_keypair`).

### Recommendation
- Short term: change `programs/bpf_loader/src/lib.rs`'s `DeployWithMaxDataLen` handler to invoke `SystemInstruction::CreateAccountAllowPrefund` (gated behind the existing `create_account_allow_prefund` feature) instead of `SystemInstruction::CreateAccount` for the ProgramData account, mirroring the mitigation already implemented in the System Program.
- Long term: audit all builtin/native programs that derive deterministic PDAs and then CPI into `system_instruction::create_account` (buffer accounts, lookup tables, stake/vote/nonce-with-seed flows, etc.) for the same pre-funding griefing pattern, and standardize on prefund-tolerant creation wherever the target address is publicly derivable before the creating transaction is signed/submitted.

### Proof of Concept
1. Attacker observes (or predicts) that a deployer intends to deploy a brand-new upgradeable program with pubkey `P` (e.g., via mempool visibility of an unconfirmed `Deploy` transaction, or by knowing the deployer uses `get_default_program_keypair`).
2. Attacker computes `programdata_address = find_program_address([P.as_ref()], bpf_loader_upgradeable::id())` — this requires no special knowledge beyond `P`.
3. Attacker submits a `SystemInstruction::Transfer` of 1 lamport from any funded account to `programdata_address` and gets it confirmed first (front-run).
4. Deployer's `DeployWithMaxDataLen` instruction executes, derives the same `programdata_address`, matches it against the account check at `programs/bpf_loader/src/lib.rs:280-285`, then CPIs `system_instruction::create_account` for it.
5. In `system_processor.rs`'s `create_account`, since `to.get_lamports() > 0`, the call returns `SystemError::AccountAlreadyInUse`, aborting the whole deploy instruction and consuming the deployer's transaction fee without deploying the program.

### Citations

**File:** programs/bpf_loader/src/lib.rs (L279-285)
```rust
            // Create ProgramData account
            let (derived_address, bump_seed) =
                Pubkey::find_program_address(&[new_program_id.as_ref()], program_id);
            if derived_address != programdata_key {
                ic_logger_msg!(log_collector, "ProgramData address is not derived");
                return Err(InstructionError::InvalidArgument);
            }
```

**File:** programs/bpf_loader/src/lib.rs (L295-310)
```rust
            let owner_id = *program_id;
            let mut instruction = system_instruction::create_account(
                &payer_key,
                &programdata_key,
                1.max(rent.minimum_balance(programdata_len)),
                programdata_len as u64,
                program_id,
            );

            // pass an extra account to avoid the overly strict UnbalancedInstruction error
            instruction
                .accounts
                .push(AccountMeta::new(buffer_key, false));

            invoke_context
                .native_invoke_signed(instruction, &[&[new_program_id.as_ref(), &[bump_seed]]])?;
```

**File:** programs/system/src/system_processor.rs (L160-174)
```rust
) -> Result<(), InstructionError> {
    // if it looks like the `to` account is already in use, bail
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        if to.get_lamports() > 0 {
            ic_msg!(
                invoke_context,
                "Create Account: account {:?} already in use",
                to_address
            );
            return Err(SystemError::AccountAlreadyInUse.into());
        }

        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
```

**File:** programs/system/src/system_processor.rs (L184-213)
```rust
/// Create a new account without checking for 0 lamports. All other checks remain.
/// Intended for use where account has already had rent paid in whole or in part
/// before creation.
#[allow(clippy::too_many_arguments)]
fn create_account_allow_prefund(
    to_account_index: IndexOfAccount,
    to_address: &Address,
    from_and_lamports: Option<(IndexOfAccount, u64)>,
    space: u64,
    owner: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
    if let Some((from_account_index, lamports)) = from_and_lamports
        && lamports > 0
    {
        transfer(
            from_account_index,
            to_account_index,
            lamports,
            invoke_context,
            instruction_context,
        )?;
    }
    Ok(())
```

**File:** programs/system/src/system_processor.rs (L245-260)
```rust
fn transfer(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    lamports: u64,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    if !instruction_context.is_instruction_account_signer(from_account_index)? {
        ic_msg!(
            invoke_context,
            "Transfer: `from` account {} must sign",
            instruction_context.get_key_of_instruction_account(from_account_index)?,
        );
        return Err(InstructionError::MissingRequiredSignature);
    }

```

**File:** programs/system/src/system_processor.rs (L530-563)
```rust
        SystemInstruction::CreateAccountAllowPrefund {
            lamports,
            space,
            owner,
        } => {
            if !invoke_context
                .get_feature_set()
                .create_account_allow_prefund
            {
                return Err(InstructionError::InvalidInstructionData);
            }
            let from_and_lamports = if lamports > 0 {
                instruction_context.check_number_of_instruction_accounts(2)?;
                Some((1, lamports))
            } else {
                instruction_context.check_number_of_instruction_accounts(1)?;
                None
            };
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(0)?,
                None,
                invoke_context,
            )?;
            create_account_allow_prefund(
                0,
                &to_address,
                from_and_lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }
```

**File:** cli/tests/program.rs (L349-384)
```rust
    // Attempt to deploy to account with excess balance
    let custom_address_keypair = Keypair::new();
    config.signers = vec![&custom_address_keypair];
    config.command = CliCommand::Airdrop {
        pubkey: None,
        // Anything over minimum_balance_for_programdata should trigger an error.
        lamports: 2 * minimum_balance_for_programdata,
    };
    process_command(&config).await.unwrap();
    config.signers = vec![&keypair, &custom_address_keypair];
    config.command = CliCommand::Program(ProgramCliCommand::Deploy {
        program_location: Some(noop_path.to_str().unwrap().to_string()),
        fee_payer_signer_index: 0,
        program_signer_index: Some(1),
        program_pubkey: None,
        buffer_signer_index: None,
        buffer_pubkey: None,
        upgrade_authority_signer_index: 0,
        is_final: true,
        max_len: None,
        skip_fee_check: false,
        compute_unit_price: None,
        max_sign_attempts: 5,
        auto_extend: true,
        use_rpc: false,
        skip_feature_verification: true,
    });
    expect_command_failure(
        &config,
        "The CLI blocks deployments into accounts that hold more than the necessary amount of SOL",
        &format!(
            "Account {} is not an upgradeable program or already in use",
            custom_address_keypair.pubkey()
        ),
    )
    .await;
```
