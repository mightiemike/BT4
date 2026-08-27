## Title
Missing signer check in `InitializeNonceAccount` allows front-running / hijacking of nonce account authority - (File: `programs/system/src/system_instruction.rs`)

## Summary
`initialize_nonce_account()` in the System program sets a nonce account's authority field without requiring any signature from the nonce account itself (or any pre-existing owner). This mirrors the reported `NoteERC20.initialize()` front-running bug class: an account is funded/created in one step, and a subsequent, unprotected "initialize" call can be raced by any unprivileged party to claim control (here, the `nonce_authority`) before the legitimate creator's initialization transaction lands.

## Finding Description
`initialize_nonce_account` only validates that the account is writable and that it is currently `State::Uninitialized`; it never checks that the target nonce account (or any specific signer) actually signed the transaction: [1](#0-0) 

This is unlike the other nonce instructions in the same file, which explicitly verify the caller controls the account/authority via the `signers` set:
- `advance_nonce_account` checks `signers.contains(&data.authority)` [2](#0-1) 
- `withdraw_nonce_account` calls `check_signer(...)` against the authority/account key [3](#0-2) 
- `authorize_nonce_account` uses `Versions::authorize(signers, ...)` which requires the current authority to sign [4](#0-3) 

`initialize_nonce_account` has no analogous check. It is invoked directly from `system_processor.rs` without any additional signer validation being layered on top: [5](#0-4) 

Because Solana enforces `is_signer` per-account based on what the *transaction sender* declares in the instruction's `AccountMeta` list (not based on ownership of the actual private key), an attacker does not need the nonce account's private key at all: they can submit their own `InitializeNonceAccount` instruction referencing the victim's already-created (but not-yet-initialized) nonce account pubkey as a non-signer account, and pass their own pubkey as the `authorized` value. Since the callee code path never checks `account.is_signer()` or any signer set, this succeeds as long as:
1. The account is owned by the System program and writable,
2. Its state is `Uninitialized`,
3. It already holds ≥ rent-exempt-minimum lamports (deposited by the legitimate creator in a prior, separate `CreateAccount` transaction).

This precisely parallels the reported bug class: the "deploy" (`CreateAccount`, funding the account) and the "initialize" (`InitializeNonceAccount`, setting the authority) are two separate transactions, and the initialize step carries no ownership/signature binding to the deployer, so it can be front-run by anyone watching the mempool for the pending `CreateAccount`/`InitializeNonceAccount` pair.

## Impact Explanation
Once an attacker wins the race and initializes the account with themselves as `nonce_authority`, they gain full spending control over the account:
- They can call `WithdrawNonceAccount`, which only requires `signers.contains(&data.authority)` — satisfied by the attacker's own signature — and drain the lamports the victim deposited for rent-exemption to an account of the attacker's choosing. [6](#0-5) 
- They can also `AdvanceNonceAccount`/`AuthorizeNonceAccount` at will, permanently denying the intended owner use of the nonce account (durable-nonce DoS).

This is a concrete theft-of-funds and account-hijack primitive reachable from an ordinary, unprivileged client transaction, not merely a griefing/no-impact issue.

## Likelihood Explanation
Exploitability depends on client behavior: if `CreateAccount` and `InitializeNonceAccount` are always submitted atomically in one transaction (as done by the CLI's bundled `create_nonce_account`/`create_nonce_account_with_seed` builders), there is no window to exploit. However, nothing in the System program enforces this atomicity — any wallet, SDK, or dApp integration that performs the two steps as separate transactions (e.g., pre-funding a nonce account address ahead of time, then initializing it later, or third-party services building nonce accounts on a user's behalf) creates an observable mempool window for a front-runner. Given the low cost of scanning pending transactions and submitting a higher-priority-fee `InitializeNonceAccount` instruction, likelihood is non-trivial for any non-atomic usage pattern.

## Recommendation
Add a signer/ownership check to `initialize_nonce_account` analogous to the other nonce instructions — e.g., require that the account itself (or a pubkey embedded at account-creation time) be present in the `signers` set before allowing the `Uninitialized -> Initialized` transition, closing the front-runnable window regardless of whether callers split account creation and initialization across transactions.

## Proof of Concept
1. Victim submits `SystemInstruction::CreateAccount` funding a fresh keypair `N` as a System-owned, `Uninitialized` nonce-sized account (rent-exempt balance), intending to follow up with `InitializeNonceAccount(victim_authority)` in a second transaction.
2. Attacker observes account `N`'s pubkey (e.g., from the confirmed `CreateAccount` transaction or a known/pre-announced address) and, before the victim's second transaction lands, submits their own transaction containing `SystemInstruction::InitializeNonceAccount(attacker_pubkey)` with account `N` listed as writable, non-signer.
3. `system_processor.rs`'s `InitializeNonceAccount` arm invokes `initialize_nonce_account`, which only checks `is_writable()` and `State::Uninitialized` — both satisfied — and sets `nonce_authority = attacker_pubkey`. [1](#0-0) 
4. The victim's subsequent `InitializeNonceAccount(victim_authority)` transaction now fails with `InstructionError::InvalidAccountData` since state is already `Initialized`. [7](#0-6) 
5. Attacker submits `SystemInstruction::WithdrawNonceAccount(all_lamports)` signed by `attacker_pubkey`, which passes the `check_signer(&data.authority)` check and transfers all deposited lamports to an attacker-controlled account. [8](#0-7)

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

**File:** programs/system/src/system_instruction.rs (L163-201)
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
```

**File:** programs/system/src/system_instruction.rs (L202-209)
```rust
        State::Initialized(_) => {
            ic_msg!(
                invoke_context,
                "Initialize nonce account: Account {} state is invalid",
                account.get_key()
            );
            Err(InstructionError::InvalidAccountData)
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

**File:** programs/system/src/system_processor.rs (L448-467)
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
        }
```
