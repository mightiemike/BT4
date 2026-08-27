### Title
Front-running / donation attack on `CreateAccount` permanently blocks legitimate account creation - ([File: programs/system/src/system_processor.rs])

### Summary
The Ethena `StakedUSDe` finding shows that an attacker can grief a protocol by donating funds directly to a target address/vault before a legitimate actor's transaction lands, causing the legitimate operation to permanently revert due to a state check that treats the pre-funded address as "already used". The System Program's `CreateAccount` instruction in agave has the exact same class of flaw: it rejects account creation whenever the destination account already holds any lamports, so an attacker can pre-fund (donate lamports to) a not-yet-created address to permanently block the intended owner from ever creating an account at that address via `CreateAccount`.

### Finding Description
`create_account` in `system_processor.rs` unconditionally fails with `SystemError::AccountAlreadyInUse` if the destination account's lamport balance is non-zero at the time the instruction executes: [1](#0-0) 

Because any unprivileged sender can transfer lamports to an arbitrary `Pubkey` via a simple `SystemInstruction::Transfer` (no signature by the target address is required to *receive* lamports), an attacker can watch the mempool/gossip for a pending `CreateAccount` transaction (or simply pre-emptively fund a known deterministic address, e.g. a PDA-derived nonce/stake/token account address) and front-run it with a 1-lamport transfer to the target `to` address. Once that transfer lands, the `to` account has `lamports > 0`, so every subsequent `CreateAccount` instruction targeting that exact address will hit the `AccountAlreadyInUse` check and fail, deterministically and permanently (a normal wallet retry with the same address will always fail).

This is structurally identical to the `StakedUSDe` `MinShares` bug: an attacker performs an unprivileged token/lamport transfer directly into the target contract/account state, corrupting an invariant ("lamports == 0" ⇔ "unused account") that a subsequent legitimate operation depends on, causing a denial of service for the legitimate user.

The agave codebase itself confirms this is a known, real problem: a new instruction, `SystemInstruction::CreateAccountAllowPrefund`, was added specifically to tolerate a pre-funded destination account (it does not check `lamports > 0`, only that the account is not owned/populated by another program), and it is gated behind the `create_account_allow_prefund` feature flag: [2](#0-1) [3](#0-2) 

The existing/legacy `SystemInstruction::CreateAccount` path used by essentially all wallets, CLIs, and on-chain programs (`stake_instruction::create_account`, `create_nonce_account`, SPL token account creation, etc.) still performs the vulnerable `lamports > 0` check and has no built-in remedy other than switching addresses (e.g. via `CreateAccountWithSeed`, which derives a fresh, unpredictable-until-committed address) — this workaround is visible throughout the CLI code (`cli/src/nonce.rs`, `cli/src/stake.rs`) which explicitly warn/guard against "account already exists" scenarios: [4](#0-3) [5](#0-4) 

### Impact Explanation
Any unprivileged party can grief a target address by donating 1 lamport to it prior to the legitimate `CreateAccount` transaction, causing the legitimate transaction to fail with `SystemError::AccountAlreadyInUse` inside the System Program (a builtin program reachable from any ordinary client transaction). Because the destination `Pubkey` for many flows is either fixed/known ahead of time or is derived deterministically (e.g. `Pubkey::create_with_seed`, PDAs used by other programs when calling `create_account` via CPI, or any keypair whose public key is disclosed/observable in a not-yet-broadcast transaction that reaches gossip/mempool before confirmation), this griefing is cheap (a single 1-lamport, single-signature transfer) and results in denial of service: the intended account can never be created at that address, forcing the victim to regenerate keys/derivations and resend funds, and potentially breaking programs whose CPI logic hard-codes a PDA and calls `create_account` on it (a program cannot simply "pick a different address" the way a CLI user can). This matches the DoS impact class validated as Medium/notable in the reference report, translated here to a builtin-program invariant in Agave's own System Program.

### Likelihood Explanation
Likelihood is high: the attack requires only a standard `Transfer` instruction signed by the attacker, sending an arbitrary small amount of lamports to a known/derivable public key — no special privileges, no race beyond ordinary transaction ordering, and no cost beyond the network fee and the (recoverable, since lamports remain in the account) donated amount. The codebase's own introduction of `CreateAccountAllowPrefund` behind a feature flag is direct evidence that the Agave/Solana developers identified and are actively mitigating this exact "prefunded account" foot-gun for `CreateAccount`, but the legacy, still-default `CreateAccount` instruction path remains exposed wherever callers have not migrated to the new instruction.

### Recommendation
- Complete rollout/activation of the `create_account_allow_prefund` feature and migrate high-value/PDA-driven callers (stake, nonce, token account creation) to `CreateAccountAllowPrefund` so pre-funded addresses do not block legitimate creation.
- For callers that must keep using `CreateAccount`, prefer `CreateAccountWithSeed`/PDA derivation schemes whose addresses are not predictable/observable until the creating transaction itself is broadcast, reducing the front-running window.
- Consider documenting/enforcing at the SDK level that any program deriving a fixed PDA and calling `create_account` via CPI should use the prefund-tolerant path once available cluster-wide.

### Proof of Concept
1. Victim generates a keypair (or derives a PDA/seeded address) `to_address` intended for `SystemInstruction::CreateAccount`, and this address becomes visible (e.g., broadcast in a pending transaction, or deterministically derivable by an observer).
2. Attacker submits `SystemInstruction::Transfer { lamports: 1 }` from any funded account to `to_address` before the victim's `CreateAccount` transaction is confirmed.
3. Victim's `CreateAccount` transaction executes `create_account` in `programs/system/src/system_processor.rs`; because `to.get_lamports() > 0`, it returns `Err(SystemError::AccountAlreadyInUse)` per lines 164–171, and the account can never be created at that address via `CreateAccount`.
4. This is directly confirmed by the existing test `test_create_already_in_use`, which asserts that any pre-existing lamport balance causes `CreateAccount` to fail with `AccountAlreadyInUse`: [6](#0-5) .

### Citations

**File:** programs/system/src/system_processor.rs (L160-171)
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
```

**File:** programs/system/src/system_processor.rs (L184-214)
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

**File:** programs/system/src/system_processor.rs (L1014-1041)
```rust
        // Attempt to create an account that already has lamports
        let owned_account = AccountSharedData::new(1, 0, &Pubkey::default());
        let unchanged_account = owned_account.clone();
        let accounts = process_instruction(
            &bincode::serialize(&SystemInstruction::CreateAccount {
                lamports: 50,
                space: 2,
                owner: new_owner,
            })
            .unwrap(),
            vec![(from, from_account), (owned_key, owned_account)],
            vec![
                AccountMeta {
                    pubkey: from,
                    is_signer: true,
                    is_writable: false,
                },
                AccountMeta {
                    pubkey: owned_key,
                    is_signer: true,
                    is_writable: false,
                },
            ],
            Err(SystemError::AccountAlreadyInUse.into()),
        );
        assert_eq!(accounts[0].lamports(), 100);
        assert_eq!(accounts[1], unchanged_account);
    }
```

**File:** cli/src/nonce.rs (L531-538)
```rust
    if let Ok(nonce_account) = get_account(rpc_client, &nonce_account_address).await {
        let err_msg = if state_from_account(&nonce_account).is_ok() {
            format!("Nonce account {nonce_account_address} already exists")
        } else {
            format!("Account {nonce_account_address} already exists and is not a nonce account")
        };
        return Err(CliError::BadParameter(err_msg).into());
    }
```

**File:** cli/src/stake.rs (L1495-1503)
```rust
    if !sign_only {
        if let Ok(stake_account) = rpc_client.get_account(&stake_account_address).await {
            let err_msg = if stake_account.owner == stake::program::id() {
                format!("Stake account {stake_account_address} already exists")
            } else {
                format!("Account {stake_account_address} already exists and is not a stake account")
            };
            return Err(CliError::BadParameter(err_msg).into());
        }
```
