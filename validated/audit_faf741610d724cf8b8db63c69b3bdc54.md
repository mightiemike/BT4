### Title
Unauthenticated `InitializeNonceAccount` instruction allows front-running of durable nonce account setup, enabling authority hijack and lamport theft - (File: programs/system/src/system_instruction.rs)

### Summary
The `SystemInstruction::InitializeNonceAccount` instruction, dispatched from `programs/system/src/system_processor.rs`, initializes an already-created, system-owned account into `State::Initialized` with an attacker/caller-supplied `authorized` pubkey, without ever verifying that the nonce account itself (or its rightful creator) has signed the initialization instruction.

### Finding Description
`SystemInstruction::InitializeNonceAccount(authorized)` is handled in `system_processor.rs` by calling `initialize_nonce_account(&mut me, &authorized, &rent, invoke_context)`. [1](#0-0)  Unlike sibling instructions in the same match arm — `AdvanceNonceAccount` and `WithdrawNonceAccount` — which both pass the transaction's `signers` set into their handlers, the `InitializeNonceAccount` branch does not pass `signers` at all, and `initialize_nonce_account`'s function signature has no `signers: &HashSet<Pubkey>` parameter. [2](#0-1) 

Inside `initialize_nonce_account`, the only checks performed are that the account is writable and that its current state is `State::Uninitialized` with sufficient rent-exempt lamports; it then unconditionally sets `State::Initialized(data)` with `nonce_authority` taken directly from instruction data, with no signer/ownership check tying this call to the account's creator. [3](#0-2) 

This is the classic "front-runnable initializer" bug class from the referenced report: a two-step create-then-initialize pattern (`SystemInstruction::CreateAccount` followed by `SystemInstruction::InitializeNonceAccount`) where the second, security-critical step is public/unauthenticated. Contrast this with `withdraw_nonce_account`, which explicitly requires `check_signer(from.get_key())` for an uninitialized account or `check_signer(&data.authority)` for an initialized one before moving funds. [4](#0-3)  `InitializeNonceAccount` has no analogous check protecting who may set the initial `authority`.

While the CLI/SDK helper (`solana_system_interface`/nonce CLI tooling) typically bundles `CreateAccount` + `InitializeNonceAccount` into one atomic transaction so no observable gap exists, the system program instruction processor itself places no such atomicity requirement — an ordinary client can legally submit `CreateAccount` and `InitializeNonceAccount` as separate transactions (e.g., staged deployment, multi-instruction batching by third-party tooling, or wallets that don't follow the bundled pattern). Any account that is system-owned, has zero data content matching `nonce::state::Versions` size, and is in `State::Uninitialized` is a valid target for anyone's `InitializeNonceAccount` transaction.

### Impact Explanation
An attacker monitoring the mempool/recent transactions can observe a `CreateAccount` transaction that allocates a nonce-account-shaped account (rent-exempt system-owned account with `nonce::state::Versions`-sized data) and front-run the victim's subsequent `InitializeNonceAccount` transaction with their own, setting themselves as `nonce_authority`. Once initialized with the attacker's authority, `withdraw_nonce_account` will accept the attacker's signature over `data.authority` and allow full lamport withdrawal from the account. [5](#0-4)  This constitutes concrete theft of funds (the rent-exempt balance/lamports deposited into the nonce account) by an unprivileged network participant, with no way for the legitimate creator to recover control since `initialize_nonce_account` rejects further initialization once `State::Initialized` is set. [6](#0-5) 

### Likelihood Explanation
Likelihood is moderate: it requires (a) a victim to submit `CreateAccount` and `InitializeNonceAccount` as separate transactions rather than atomically bundled (a real but non-default usage pattern), and (b) an attacker to observe and front-run the intervening window before the victim's `InitializeNonceAccount` lands. Standard CLI/SDK helpers bundle both instructions atomically, which mitigates but does not eliminate the exposure for any tooling or user that does not follow that convention, since the system program itself enforces no atomicity or signer binding between account creation and nonce initialization.

### Recommendation
Require `initialize_nonce_account` to verify that the nonce account's own key (or its designated creator) is present in the transaction's `signers` set, mirroring the signer requirement already used in `withdraw_nonce_account` and `advance_nonce_account`, so that only the party that legitimately controls (signed for) account creation/initialization can set the initial `nonce_authority`.

### Proof of Concept
1. Victim submits `SystemInstruction::CreateAccount` in transaction T1 to allocate a system-owned account `N` sized for `nonce::state::Versions`, funded with rent-exempt lamports.
2. Victim intends to submit `SystemInstruction::InitializeNonceAccount(victim_authority)` for `N` in a follow-up transaction T2.
3. Attacker observes T1 land on-chain (state is `Uninitialized`, system-owned, correctly sized) and submits `SystemInstruction::InitializeNonceAccount(attacker_authority)` for `N` before T2 confirms — this call passes all checks in `initialize_nonce_account` (writable, `Uninitialized`, sufficient lamports) since none require the caller to be the creator. [3](#0-2) 
4. Victim's T2 now fails with `InstructionError::InvalidAccountData` because state is already `Initialized`. [7](#0-6) 
5. Attacker submits `SystemInstruction::WithdrawNonceAccount(all_lamports)` signed by `attacker_authority`, which passes `check_signer(&data.authority)` and drains `N`'s lamports to an attacker-controlled account. [8](#0-7)

### Citations

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

**File:** programs/system/src/system_instruction.rs (L99-158)
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
        }
    };

    from.checked_sub_lamports(lamports)?;
    drop(from);
    let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
    to.checked_add_lamports(lamports)?;
```

**File:** programs/system/src/system_instruction.rs (L163-177)
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

```

**File:** programs/system/src/system_instruction.rs (L178-201)
```rust
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

**File:** programs/system/src/system_instruction.rs (L202-210)
```rust
        State::Initialized(_) => {
            ic_msg!(
                invoke_context,
                "Initialize nonce account: Account {} state is invalid",
                account.get_key()
            );
            Err(InstructionError::InvalidAccountData)
        }
    }
```
