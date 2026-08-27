### Title
Unauthenticated `InitializeNonceAccount` allows front-running of nonce account setup, permanently DoS-ing legitimate initialization - ([File: programs/system/src/system_instruction.rs])

### Summary
The System Program's `initialize_nonce_account()` function sets a nonce account's authority based on an instruction argument without requiring any signature from the account being initialized, its intended authority, or any other privileged party. Because the state machine only permits a transition from `State::Uninitialized` once and rejects any subsequent initialize as `InvalidAccountData`, any external caller can race a legitimate initialization and lock in an attacker-controlled authority, permanently breaking the intended setup — directly mirroring the `AdminStrategy.connect()` DoS pattern in the referenced report, where an unauthenticated call locks state and reverts the legitimate call path.

### Finding Description
`initialize_nonce_account()` only checks that the target account is writable, sufficiently funded for rent-exemption, and currently `State::Uninitialized`. It performs **no signer check at all** for the nonce account, the incoming `nonce_authority`, or any privileged relationship between the caller and the account: [1](#0-0) 

The processor dispatch for `SystemInstruction::InitializeNonceAccount` similarly does not require the account at index 0 to be a transaction signer: [2](#0-1) 

Once initialized, any later attempt to re-run the same instruction against the account is rejected outright: [3](#0-2) 

This is structurally identical to the `AdminStrategy.connect()` issue: a state-setting function reachable by anyone, gated only by an "already set" check, with no `msg.sender`/signer validation tying the call to a trusted initializer. If a nonce account is allocated (e.g., via `CreateAccount`/`CreateAccountWithSeed`) in a transaction separate from its `InitializeNonceAccount` call — which is possible whenever the account address is known/derivable ahead of the initializing transaction (e.g., `create_account_with_seed`, pre-funded accounts, or any workflow that doesn't atomically bundle creation+init in one transaction) — any unprivileged party observing the pending create transaction can submit `InitializeNonceAccount` first, setting a malicious `nonce_authority`. The legitimate initialize call then fails permanently with `InstructionError::InvalidAccountData`, and the account is now controlled by the attacker's authority, bricking the intended nonce workflow (a durable-nonce DoS on the victim). The exact same missing-signer-check pattern also exists in `bpf_loader`'s `InitializeBuffer` handler: [4](#0-3) 

### Impact Explanation
This is a Medium-severity DoS analog: any account whose creation and initialization are not atomically bundled in the same transaction can be permanently hijacked/bricked by an unrelated unprivileged sender who merely observes the pending creation. This breaks the victim's setup flow (durable nonces, buffer/program deployment) and forces recovery via a new account, exactly mirroring the referenced report's "irreversibly brick protocol setup" impact.

### Likelihood Explanation
Likelihood is Medium: it requires the attacker to observe an account-creation transaction in flight (mempool/gossip) before its paired initialize instruction lands, and it only matters when creation and initialization are not atomically combined in one transaction. Standard CLI flows (`solana-cli`'s `create_nonce_account`, `create_buffer`) bundle create+init atomically, closing the window in that path, but the protocol-level instruction handlers themselves impose no such guarantee, so any third-party tooling, custom program, or workflow that splits these steps is exposed.

### Recommendation
Add an explicit authorization check to `initialize_nonce_account()` (and the analogous `InitializeBuffer` handler) requiring that the account being initialized (or a designated creator/authority passed at account-creation time) be a signer on the initializing instruction, so an unrelated party cannot race and claim ownership of an account it did not create.

### Proof of Concept
1. Victim submits transaction A: `CreateAccountWithSeed(base, seed, ...)` allocating a nonce-account-sized, system-owned account at a derived address `nonce_pubkey`, intending to follow up with `InitializeNonceAccount(nonce_pubkey, victim_authority)` in transaction B.
2. Attacker observes transaction A propagate/land (address `nonce_pubkey` is now computable/visible) before transaction B is confirmed.
3. Attacker submits `InitializeNonceAccount(nonce_pubkey, attacker_authority)` referencing the same account, with no signature required from `nonce_pubkey` itself — dispatch path in `programs/system/src/system_processor.rs:448-467` and handler in `programs/system/src/system_instruction.rs:163-211` accept it since state is `Uninitialized` and lamports/writable checks pass.
4. Victim's transaction B now fails with `InstructionError::InvalidAccountData` (per `system_instruction.rs:202-210`), because state is no longer `Uninitialized`.
5. The account is now a nonce account under `attacker_authority`; the victim cannot use it as intended and must abandon it, matching the "connect() DoS" impact of the source report.

### Citations

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

**File:** programs/bpf_loader/src/lib.rs (L158-172)
```rust
        UpgradeableLoaderInstruction::InitializeBuffer => {
            instruction_context.check_number_of_instruction_accounts(2)?;
            let mut buffer = instruction_context.try_borrow_instruction_account(0)?;

            if UpgradeableLoaderState::Uninitialized != buffer.get_state()? {
                ic_logger_msg!(log_collector, "Buffer account already initialized");
                return Err(InstructionError::AccountAlreadyInitialized);
            }

            let authority_key = Some(*instruction_context.get_key_of_instruction_account(1)?);

            buffer.set_state(&UpgradeableLoaderState::Buffer {
                authority_address: authority_key,
            })?;
        }
```
