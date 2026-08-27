### Title
Front-runnable `InitializeBuffer` in BPF Loader Upgradeable allows theft of buffer rent-exempt lamports - (File: programs/bpf_loader/src/lib.rs)

### Summary
The `bpf_loader_upgradeable` program's `InitializeBuffer` instruction sets the buffer account's authority from whatever pubkey is passed as instruction account #1, without requiring that account, or the buffer account itself, to be a transaction signer. Client tooling only mitigates this by convention — bundling `system_instruction::create_account` and `InitializeBuffer` into the same transaction/message — but nothing in the runtime enforces this atomicity. Any ordinary user can observe an in-flight `CreateAccount` transaction that allocates a fresh account owned by `bpf_loader_upgradeable`, and front-run the legitimate deployer's `InitializeBuffer` call with their own transaction naming themselves as the buffer authority, exactly mirroring the reported `init()` frontrunning bug class.

### Finding Description
`process_loader_upgradeable_instruction` handles `UpgradeableLoaderInstruction::InitializeBuffer` as follows: [1](#0-0) 

There is no check that the buffer account (`instruction_context` account index 0) is a signer, nor that the caller supplying the `authority_key` (account index 1) has any relationship to whoever funded/created the buffer account. The only state precondition is `UpgradeableLoaderState::Uninitialized`, which is exactly the state a freshly `CreateAccount`-allocated account (with `owner = bpf_loader_upgradeable::id()`, zeroed data) is in immediately after account creation, before `InitializeBuffer` runs [2](#0-1) .

Because Solana processes transactions independently (there is no built-in guarantee that `CreateAccount` and `InitializeBuffer` land in the same transaction), a malicious actor monitoring the mempool/blocks for a `CreateAccount` instruction assigning ownership to `bpf_loader_upgradeable` can submit their own `InitializeBuffer` instruction referencing that buffer pubkey and their own pubkey as authority before the legitimate deployer's `InitializeBuffer` transaction lands. This is the exact analog of the reported bug: the "init" function (here, `InitializeBuffer`) is callable in a separate transaction from account creation, with no atomicity guarantee, and no signature/ownership binding tying the initializer to the creator.

Client helpers such as `solana_loader_v3_interface::instruction::create_buffer` and the CLI's buffer-write flow only mitigate this by convention, bundling both instructions into one message [3](#0-2) , but this is not enforced by the protocol/runtime itself — any client (or attacker) can submit these as separate transactions.

Once the attacker becomes the buffer authority, they can subsequently call `Close`, which for a `Buffer`-state account transfers all lamports held by the buffer to a recipient chosen by whoever signs as the current authority: [4](#0-3) 

Since the attacker is now the authority, they alone can sign a `Close` (via `common_close_account`) and redirect the account's rent-exempt lamports — funded by the legitimate deployer's `CreateAccount` — to their own account, achieving direct theft of funds. The legitimate deployer's subsequent `Write`/`DeployWithMaxDataLen` calls will also fail because `Write` requires the caller-provided authority to match `authority_address` and sign [5](#0-4) , so the deployer cannot recover control of the buffer.

### Impact Explanation
This results in direct theft of funds: an attacker can capture the rent-exempt lamports paid into a program buffer account by an ordinary, unprivileged client and drain them via `Close`, while simultaneously denying the legitimate deployer the ability to use that buffer to deploy or upgrade their program. This matches the "concrete theft of funds" bar for a valid finding.

### Likelihood Explanation
Exploitation requires only observing a `CreateAccount` instruction (public/broadcast) targeting an account owned by `bpf_loader_upgradeable` and quickly submitting a competing `InitializeBuffer` transaction with a higher priority fee/compute price — a standard front-running technique requiring no special privileges, consistent with the "unprivileged-sender" scope of this analysis. Any tool or user that does not carefully bundle `CreateAccount` + `InitializeBuffer` atomically (or that relies on separate transactions, e.g., due to size limits or multi-step signing flows) is exposed.

### Recommendation
Require the `InitializeBuffer` instruction to enforce a binding between the creator and the initializer — e.g., require the buffer account to be a signer on `InitializeBuffer` (proving control derives from the same transaction that created it), or otherwise ensure `CreateAccount` and `InitializeBuffer` cannot be observed/separated by an attacker (e.g., via a single combined instruction that atomically creates-and-initializes the buffer account within loader program logic, using CPI to the system program instead of two independently-submittable instructions).

### Proof of Concept
1. Victim submits a transaction containing `system_instruction::create_account(payer, buffer_pubkey, min_rent_lamports, buffer_size, bpf_loader_upgradeable::id())`.
2. Attacker observes this transaction in the mempool/gossip and, before the victim's follow-up `InitializeBuffer` instruction (naming `victim_authority`) is confirmed, submits `InitializeBuffer` with accounts `[buffer_pubkey (writable, non-signer), attacker_pubkey (non-signer)]`, paying a higher fee.
3. `process_loader_upgradeable_instruction`'s `InitializeBuffer` arm succeeds because the buffer state is `Uninitialized` and there is no signer/authority check on either account [1](#0-0) ; the buffer's `authority_address` becomes `attacker_pubkey`.
4. Victim's original `InitializeBuffer` transaction fails (`AccountAlreadyInitialized`), and any subsequent `Write` from the victim fails `IncorrectAuthority` since `authority_address != victim_authority` [6](#0-5) .
5. Attacker calls `Close` with themselves as authority and their own account as recipient, draining the buffer's rent-exempt lamports funded by the victim [4](#0-3) .

### Citations

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

**File:** programs/bpf_loader/src/lib.rs (L177-190)
```rust
            if let UpgradeableLoaderState::Buffer { authority_address } = buffer.get_state()? {
                if authority_address.is_none() {
                    ic_logger_msg!(log_collector, "Buffer is immutable");
                    return Err(InstructionError::Immutable); // TODO better error code
                }
                let authority_key = Some(*instruction_context.get_key_of_instruction_account(1)?);
                if authority_address != authority_key {
                    ic_logger_msg!(log_collector, "Incorrect buffer authority provided");
                    return Err(InstructionError::IncorrectAuthority);
                }
                if !instruction_context.is_instruction_account_signer(1)? {
                    ic_logger_msg!(log_collector, "Buffer authority did not sign");
                    return Err(InstructionError::MissingRequiredSignature);
                }
```

**File:** programs/bpf_loader/src/lib.rs (L710-716)
```rust
                UpgradeableLoaderState::Buffer { authority_address } => {
                    instruction_context.check_number_of_instruction_accounts(3)?;
                    drop(close_account);
                    common_close_account(&authority_address, &instruction_context, &log_collector)?;

                    ic_logger_msg!(log_collector, "Closed Buffer {}", close_key);
                }
```

**File:** runtime/src/loader_utils.rs (L88-107)
```rust
    bank_client
        .send_and_confirm_message(
            &[from_keypair, buffer_keypair],
            Message::new(
                &solana_loader_v3_interface::instruction::create_buffer(
                    &from_keypair.pubkey(),
                    &buffer_pubkey,
                    &buffer_authority_pubkey,
                    1.max(
                        bank_client
                            .get_minimum_balance_for_rent_exemption(program_buffer_bytes)
                            .unwrap(),
                    ),
                    program.len(),
                )
                .unwrap(),
                Some(&from_keypair.pubkey()),
            ),
        )
        .unwrap();
```
