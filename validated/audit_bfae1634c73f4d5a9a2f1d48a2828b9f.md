This confirms the vulnerability. `SystemInstruction::InitializeNonceAccount` performs no signer check whatsoever — unlike every other nonce-related instruction (`AdvanceNonceAccount`, `WithdrawNonceAccount`, `AuthorizeNonceAccount`) which explicitly check that the account authority (or the account itself in the uninitialized withdraw case) is present in the `signers` set.

### Title
Unauthorized Hijack of Uninitialized, Pre-funded Nonce Accounts via Missing Signer Check in `InitializeNonceAccount` - (File: `programs/system/src/system_processor.rs`)

### Summary
`SystemInstruction::InitializeNonceAccount` can be invoked by any unprivileged transaction sender for *any* system-owned, correctly-sized, rent-exempt account that is still in `State::Uninitialized`, without that account (or any related key) being required to sign the transaction. This is the exact "unrestricted privileged-initialization of a pre-funded resource" bug class described in the external report: a resource is funded/created first, and a second, unrestricted "initialize" step determines who controls the value — allowing a front-runner to seize control instead of the intended owner.

### Finding Description
In `programs/system/src/system_processor.rs`, the dispatch arm for `SystemInstruction::InitializeNonceAccount` does not pass the `signers` set to the handler at all: [1](#0-0) . This directly calls `initialize_nonce_account`, whose implementation in `programs/system/src/system_instruction.rs` only checks that the account is writable and in `State::Uninitialized` before setting the caller-supplied `authorized` pubkey as the new nonce authority — there is no check that the nonce account key, its creator, or any specific party has signed: [2](#0-1) .

This is inconsistent with every sibling nonce instruction in the same file, which all require the relevant authority to be a signer: `advance_nonce_account` requires `signers.contains(&data.authority)` [3](#0-2) ; `withdraw_nonce_account` requires `check_signer` on either the account key (uninitialized) or the authority (initialized) [4](#0-3) ; `authorize_nonce_account` returns `MissingRequiredSignature` when the current authority hasn't signed [5](#0-4) .

A nonce account can legitimately reach an on-chain `State::Uninitialized`, system-owned, rent-exempt state as a standalone, separately-confirmed transaction — e.g., via `SystemInstruction::CreateAccountWithSeed`, which only requires the `base` keypair (not the derived nonce address) to sign [6](#0-5) , or via a plain `CreateAccount`/allocate+assign flow submitted in a transaction that does not also bundle `InitializeNonceAccount`. Real-world tooling (e.g., `solana-cli`'s `create-nonce-account`) normally batches `CreateAccount` + `InitializeNonceAccount` atomically [7](#0-6) , but the protocol itself does not enforce atomicity, so any wallet, program, or user that splits the two steps (or whose second transaction is delayed/dropped and retried) exposes a fully-funded, uninitialized nonce account on-chain.

### Impact Explanation
Once an attacker front-runs the legitimate initialization by submitting `InitializeNonceAccount(attacker_pubkey)` for the victim's uninitialized nonce account, the account transitions to `State::Initialized` with the attacker as `authority`. The legitimate creator's own follow-up `InitializeNonceAccount` will now fail (`InstructionError::InvalidAccountData`, since the account is no longer uninitialized). The attacker, now holding nonce authority, can subsequently sign `WithdrawNonceAccount` to drain the account's entire rent-exempt lamport balance to an address of their choosing [8](#0-7) , and/or manipulate the durable nonce via `AdvanceNonceAccount`/`AuthorizeNonceAccount`, hijacking durable-nonce transaction flows relying on this account. This is a concrete theft-of-funds and privilege-escalation vulnerability against ordinary system-program clients.

### Likelihood Explanation
Exploitation only requires observing a system-owned account of the exact nonce-state size sitting in `State::Uninitialized` with a nonzero (rent-exempt) balance and submitting a single, cheap `InitializeNonceAccount` instruction naming attacker-controlled `authorized` — no private key for the nonce account, no CPI privilege, and no special timing precision beyond ordinary transaction ordering/priority is required. Any deployment pattern (multi-step wallet UX, program-driven nonce provisioning, `CreateAccountWithSeed`-based nonce derivation, or a dropped/retried second transaction) that leaves this window open is directly exploitable by any unprivileged network participant.

### Recommendation
Require the `to`-be-initialized nonce account (or its designated base/creator) to be a transaction signer in `initialize_nonce_account`, mirroring the signer checks already present in `advance_nonce_account`, `withdraw_nonce_account`, and `authorize_nonce_account`. At minimum, the `SystemInstruction::InitializeNonceAccount` dispatch arm in `system_processor.rs` should verify `signers.contains(nonce_account.get_key())` before allowing the state transition from `Uninitialized` to `Initialized`.

### Proof of Concept
1. Attacker monitors the ledger for system-owned accounts of size `NonceState::size()`, balance ≥ rent-exempt minimum, and state `State::Uninitialized` (produced e.g. by a `CreateAccountWithSeed` transaction that a victim wallet submitted separately from its `InitializeNonceAccount` step).
2. Attacker submits a transaction containing only `SystemInstruction::InitializeNonceAccount(attacker_pubkey)` referencing the victim's nonce account address, the `RecentBlockhashes` sysvar, and the `Rent` sysvar — no signature over the nonce account itself is required, only fee-payer signature of the attacker's own transaction.
3. The processor executes `initialize_nonce_account`, which succeeds because it only checks writability and `State::Uninitialized`, setting `authority = attacker_pubkey` [9](#0-8) .
4. The victim's subsequent `InitializeNonceAccount` transaction now fails with `InstructionError::InvalidAccountData`.
5. Attacker submits `SystemInstruction::WithdrawNonceAccount` signed by `attacker_pubkey`, draining the account's lamports to an attacker-controlled address [10](#0-9) .

### Citations

**File:** programs/system/src/system_processor.rs (L354-378)
```rust
        SystemInstruction::CreateAccountWithSeed {
            base,
            seed,
            lamports,
            space,
            owner,
        } => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let to_address = Address::create(
                instruction_context.get_key_of_instruction_account(1)?,
                Some((&base, &seed, &owner)),
                invoke_context,
            )?;
            create_account(
                0,
                1,
                &to_address,
                lamports,
                space,
                &owner,
                &signers,
                invoke_context,
                &instruction_context,
            )
        }
```

**File:** programs/system/src/system_processor.rs (L448-466)
```rust
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
```

**File:** programs/system/src/system_instruction.rs (L40-49)
```rust
    match state.state() {
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

**File:** programs/system/src/system_instruction.rs (L80-161)
```rust
pub(crate) fn withdraw_nonce_account(
    from_account_index: IndexOfAccount,
    lamports: u64,
    to_account_index: IndexOfAccount,
    rent: &Rent,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    let mut from = instruction_context.try_borrow_instruction_account(from_account_index)?;
    if !from.is_writable() {
        ic_msg!(
            invoke_context,
            "Withdraw nonce account: Account {} must be writeable",
            from.get_key()
        );
        return Err(InstructionError::InvalidArgument);
    }

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
        }
    };

    from.checked_sub_lamports(lamports)?;
    drop(from);
    let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
    to.checked_add_lamports(lamports)?;

    Ok(())
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

**File:** programs/system/src/system_instruction.rs (L213-248)
```rust
pub(crate) fn authorize_nonce_account(
    account: &mut BorrowedInstructionAccount,
    nonce_authority: &Pubkey,
    signers: &HashSet<Pubkey>,
    invoke_context: &InvokeContext,
) -> Result<(), InstructionError> {
    if !account.is_writable() {
        ic_msg!(
            invoke_context,
            "Authorize nonce account: Account {} must be writeable",
            account.get_key()
        );
        return Err(InstructionError::InvalidArgument);
    }
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
