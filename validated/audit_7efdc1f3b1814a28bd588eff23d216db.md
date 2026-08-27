### Title
`InitializeNonceAccount` performs no signature check, allowing front-running theft of nonce account funds - (File: `programs/system/src/system_instruction.rs`)

### Summary
The System Program's `InitializeNonceAccount` instruction sets the permanent nonce `authority` on an account without requiring any signature from the account itself, its creator, or any related party. When a durable-nonce account is created (`CreateAccount`) and initialized (`InitializeNonceAccount`) as two separate transactions instead of atomically, an attacker who observes the funded-but-uninitialized account on-chain can race to submit their own `InitializeNonceAccount(attacker_pubkey)` instruction first, permanently becoming the nonce authority and then draining the account's lamports via `WithdrawNonceAccount`. This mirrors the reported ETH2 deposit-contract bug class exactly: a "create/fund" step is signature-protected, but the subsequent "set credentials/authority" step is not, enabling a front-run that hijacks the funded resource.

### Finding Description
`initialize_nonce_account` in `programs/system/src/system_instruction.rs` takes no `signers: &HashSet<Pubkey>` parameter at all, and performs zero authorization check before writing the caller-supplied `nonce_authority` into account state: [1](#0-0) 

Compare this to every other nonce-mutating function in the same file, which explicitly requires the relevant party to be present in `signers`:
- `advance_nonce_account` requires `signers.contains(&data.authority)`. [2](#0-1) 
- `withdraw_nonce_account` requires `check_signer(from.get_key())` (uninitialized case) or `check_signer(&data.authority)` (initialized case). [3](#0-2) 
- `authorize_nonce_account` requires the current authority to sign via `Versions::authorize(signers, ...)`. [4](#0-3) 

The dispatch code in `system_processor.rs` confirms `InitializeNonceAccount` is invoked without ever passing the transaction's `signers` set, unlike the sibling handlers for `AdvanceNonceAccount`, `WithdrawNonceAccount`, and `AuthorizeNonceAccount` which all pass `&signers`: [5](#0-4) 

The only preconditions to call `initialize_nonce_account` successfully are: the target account is writable, owned by the System Program, currently deserializes to `State::Uninitialized` (i.e., zeroed/blank data of the correct size), and holds at least the rent-exempt minimum balance. None of these preconditions require the caller to be the account's creator, funder, or intended owner.

The standard, safe usage pattern bundles `CreateAccount` + `InitializeNonceAccount` atomically in one transaction/message, as seen in `create_nonce_account` / `create_nonce_account_with_seed` used by the CLI: [6](#0-5) 

Atomicity is what currently prevents exploitation in the common CLI-generated case. But nothing in the protocol enforces this bundling — `CreateAccount` (which does require the new account to sign, via `Address::is_signer`/`allocate`) can legitimately be submitted alone, or on retry after a partial failure, or by any client/integration that submits the two instructions in separate transactions: [7](#0-6) 

Once such a funded-but-uninitialized account exists on-chain (visible to anyone, e.g., via RPC polling or in-flight-transaction observation), any attacker can submit `InitializeNonceAccount(attacker_pubkey)` against it first. Since `State::Initialized` cannot be re-initialized (`initialize_nonce_account` returns `InstructionError::InvalidAccountData` if already initialized), the legitimate owner's later `InitializeNonceAccount` attempt fails, and the attacker now holds the sole `authority` over the account and its lamports, which they can extract via `WithdrawNonceAccount` by signing as that authority.

### Impact Explanation
This is a direct theft-of-funds vector: the attacker permanently and irreversibly acquires unilateral control (the `authority` field) over another party's funded System-owned account, and can withdraw all lamports from it via `withdraw_nonce_account`'s authority-based signer check. This satisfies "concrete theft of funds" and "CPI or account privilege escalation" criteria, since account authority (a privileged field) is hijacked without any signature from a legitimate party.

### Likelihood Explanation
Exploitation requires only that a `CreateAccount` (sized/owned correctly for a nonce account, funded to at least rent-exempt minimum) be observable on-chain or in the mempool before its corresponding `InitializeNonceAccount` transaction lands — i.e., the two steps are not submitted atomically in the same transaction. This can occur due to client bugs, partial transaction failures/retries, non-standard tooling that separates account creation from initialization, or deliberate multi-step account provisioning flows. While the official CLI bundles both instructions atomically (mitigating the common path), the protocol itself provides no enforcement of this bundling, and any ordinary unprivileged validator client transaction can perform the exploit — no special privileges are required.

### Recommendation
Require `InitializeNonceAccount` to check that the account being initialized (or another explicitly authorized party) is present in `signers`, mirroring the pattern already used by `advance_nonce_account`, `withdraw_nonce_account`, and `authorize_nonce_account`. At minimum, require `signers.contains(account.get_key())` before allowing `State::Uninitialized -> State::Initialized` transition, so that only the account (or its legitimate controller) can set its own authority.

### Proof of Concept
1. Victim submits transaction A: `SystemInstruction::CreateAccount { lamports: rent_exempt_min, space: nonce::state::State::size(), owner: system_program::id() }` from `victim_from` to new account `nonce_pubkey` (signed by `nonce_pubkey` and `victim_from`). This is legitimate and required to be signed, per `allocate`/`assign` checks in `system_processor.rs` lines 75-135.
2. Attacker observes `nonce_pubkey` on-chain once transaction A confirms (it is now System-owned, `State::Uninitialized`, funded ≥ rent-exempt minimum).
3. Before the victim's follow-up transaction B (`SystemInstruction::InitializeNonceAccount(victim_authority)`) lands, attacker submits transaction C: `SystemInstruction::InitializeNonceAccount(attacker_pubkey)` targeting the same `nonce_pubkey`, requiring no signature from `nonce_pubkey` or the victim (per `initialize_nonce_account`, `programs/system/src/system_instruction.rs:163-211`, and the processor dispatch at `system_processor.rs:448-466`, which passes no `signers`).
4. Transaction C lands first (or the victim's transaction B fails afterward with `InstructionError::InvalidAccountData` since the account is now `Initialized`).
5. Attacker signs and submits `SystemInstruction::WithdrawNonceAccount(all_lamports)` as the now-set authority (`attacker_pubkey`), passing `check_signer(&data.authority)` in `withdraw_nonce_account` (`system_instruction.rs:99-151`), draining the victim's deposited lamports.

### Citations

**File:** programs/system/src/system_instruction.rs (L41-49)
```rust
        State::Initialized(data) => {
            if !signers.contains(&data.authority) {
                ic_msg!(
                    invoke_context,
                    "Advance nonce account: Account {} must be a signer",
                    data.authority
                );
                return Err(InstructionError::MissingRequiredSignature);
            }
```

**File:** programs/system/src/system_instruction.rs (L99-151)
```rust
    let check_signer = |signer: &Pubkey| {
        if !signers.contains(signer) {
            ic_msg!(
                invoke_context,
                "Withdraw nonce account: Account {} must sign",
                signer
            );
            return Err(InstructionError::MissingRequiredSignature);
        }
        Ok(())
    };

    let state: Versions = from.get_state()?;
    match state.state() {
        State::Uninitialized => {
            if lamports > from.get_lamports() {
                ic_msg!(
                    invoke_context,
                    "Withdraw nonce account: insufficient lamports {}, need {}",
                    from.get_lamports(),
                    lamports,
                );
                return Err(InstructionError::InsufficientFunds);
            }
            check_signer(from.get_key())?;
        }
        State::Initialized(data) => {
            if lamports == from.get_lamports() {
                let durable_nonce =
                    DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash);
                if data.durable_nonce == durable_nonce {
                    ic_msg!(
                        invoke_context,
                        "Withdraw nonce account: nonce can only advance once per slot"
                    );
                    return Err(SystemError::NonceBlockhashNotExpired.into());
                }
                check_signer(&data.authority)?;
                from.set_state(&Versions::new(State::Uninitialized))?;
            } else {
                let min_balance = rent.minimum_balance(from.get_data().len());
                let amount = checked_add(lamports, min_balance)?;
                if amount > from.get_lamports() {
                    ic_msg!(
                        invoke_context,
                        "Withdraw nonce account: insufficient lamports {}, need {}",
                        from.get_lamports(),
                        amount,
                    );
                    return Err(InstructionError::InsufficientFunds);
                }
                check_signer(&data.authority)?;
            }
```

**File:** programs/system/src/system_instruction.rs (L163-211)
```rust
pub(crate) fn initialize_nonce_account(
    account: &mut BorrowedInstructionAccount,
    nonce_authority: &Pubkey,
    rent: &Rent,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !account.is_writable() {
        ic_msg!(
            invoke_context,
            "Initialize nonce account: Account {} must be writeable",
            account.get_key()
        );
        return Err(InstructionError::InvalidArgument);
    }

    match account.get_state::<Versions>()?.state() {
        State::Uninitialized => {
            let min_balance = rent.minimum_balance(account.get_data().len());
            if account.get_lamports() < min_balance {
                ic_msg!(
                    invoke_context,
                    "Initialize nonce account: insufficient lamports {}, need {}",
                    account.get_lamports(),
                    min_balance
                );
                return Err(InstructionError::InsufficientFunds);
            }
            let durable_nonce =
                DurableNonce::from_blockhash(&invoke_context.environment_config.blockhash);
            let data = nonce::state::Data::new(
                *nonce_authority,
                durable_nonce,
                invoke_context
                    .environment_config
                    .blockhash_lamports_per_signature,
            );
            let state = State::Initialized(data);
            account.set_state(&Versions::new(state))
        }
        State::Initialized(_) => {
            ic_msg!(
                invoke_context,
                "Initialize nonce account: Account {} state is invalid",
                account.get_key()
            );
            Err(InstructionError::InvalidAccountData)
        }
    }
}
```

**File:** programs/system/src/system_instruction.rs (L227-248)
```rust
    match account
        .get_state::<Versions>()?
        .authorize(signers, *nonce_authority)
    {
        Ok(versions) => account.set_state(&versions),
        Err(AuthorizeNonceError::Uninitialized) => {
            ic_msg!(
                invoke_context,
                "Authorize nonce account: Account {} state is invalid",
                account.get_key()
            );
            Err(InstructionError::InvalidAccountData)
        }
        Err(AuthorizeNonceError::MissingRequiredSignature(account_authority)) => {
            ic_msg!(
                invoke_context,
                "Authorize nonce account: Account {} must sign",
                account_authority
            );
            Err(InstructionError::MissingRequiredSignature)
        }
    }
```

**File:** programs/system/src/system_processor.rs (L75-100)
```rust
fn allocate(
    account: &mut BorrowedInstructionAccount,
    address: &Address,
    space: u64,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !address.is_signer(signers) {
        ic_msg!(
            invoke_context,
            "Allocate: 'to' account {:?} must sign",
            address
        );
        return Err(InstructionError::MissingRequiredSignature);
    }

    // if it looks like the `to` account is already in use, bail
    //   (note that the id check is also enforced by message_processor)
    if !account.get_data().is_empty() || !system_program::check_id(account.get_owner()) {
        ic_msg!(
            invoke_context,
            "Allocate: account {:?} already in use",
            address
        );
        return Err(SystemError::AccountAlreadyInUse.into());
    }
```

**File:** programs/system/src/system_processor.rs (L410-472)
```rust
        SystemInstruction::AdvanceNonceAccount => {
            instruction_context.check_number_of_instruction_accounts(1)?;
            let mut me = instruction_context.try_borrow_instruction_account(0)?;
            #[allow(deprecated)]
            let recent_blockhashes = get_sysvar_with_account_check::recent_blockhashes(
                invoke_context,
                &instruction_context,
                1,
            )?;
            if recent_blockhashes.is_empty() {
                ic_msg!(
                    invoke_context,
                    "Advance nonce account: recent blockhash list is empty",
                );
                return Err(SystemError::NonceNoRecentBlockhashes.into());
            }
            advance_nonce_account(&mut me, &signers, invoke_context)
        }
        SystemInstruction::WithdrawNonceAccount(lamports) => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            #[allow(deprecated)]
            let _recent_blockhashes = get_sysvar_with_account_check::recent_blockhashes(
                invoke_context,
                &instruction_context,
                2,
            )?;
            let rent =
                get_sysvar_with_account_check::rent(invoke_context, &instruction_context, 3)?;
            withdraw_nonce_account(
                0,
                lamports,
                1,
                &rent,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }
        SystemInstruction::InitializeNonceAccount(authorized) => {
            instruction_context.check_number_of_instruction_accounts(1)?;
            let mut me = instruction_context.try_borrow_instruction_account(0)?;
            #[allow(deprecated)]
            let recent_blockhashes = get_sysvar_with_account_check::recent_blockhashes(
                invoke_context,
                &instruction_context,
                1,
            )?;
            if recent_blockhashes.is_empty() {
                ic_msg!(
                    invoke_context,
                    "Initialize nonce account: recent blockhash list is empty",
                );
                return Err(SystemError::NonceNoRecentBlockhashes.into());
            }
            let rent =
                get_sysvar_with_account_check::rent(invoke_context, &instruction_context, 2)?;
            initialize_nonce_account(&mut me, &authorized, &rent, invoke_context)
        }
        SystemInstruction::AuthorizeNonceAccount(nonce_authority) => {
            instruction_context.check_number_of_instruction_accounts(1)?;
            let mut me = instruction_context.try_borrow_instruction_account(0)?;
            authorize_nonce_account(&mut me, &nonce_authority, &signers, invoke_context)
        }
```

**File:** cli/src/nonce.rs (L486-514)
```rust
    let build_message = |lamports| {
        let ixs = if let Some(seed) = seed.clone() {
            create_nonce_account_with_seed(
                &config.signers[0].pubkey(), // from
                &nonce_account_address,      // to
                &nonce_account_pubkey,       // base
                &seed,                       // seed
                &nonce_authority,
                lamports,
            )
            .with_memo(memo)
            .with_compute_unit_config(&ComputeUnitConfig {
                compute_unit_price,
                compute_unit_limit,
            })
        } else {
            create_nonce_account(
                &config.signers[0].pubkey(),
                &nonce_account_pubkey,
                &nonce_authority,
                lamports,
            )
            .with_memo(memo)
            .with_compute_unit_config(&ComputeUnitConfig {
                compute_unit_price,
                compute_unit_limit,
            })
        };
        Message::new(&ixs, Some(&config.signers[0].pubkey()))
```
