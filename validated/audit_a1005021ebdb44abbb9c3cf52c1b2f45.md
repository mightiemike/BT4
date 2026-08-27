### Title
`SystemInstruction::CreateAccount` permanently blockable by pre-funding target address with dust lamports (address-squatting DoS) - ([File: programs/system/src/system_processor.rs])

### Summary
The System program's `create_account()` refuses to create an account at an address that already has a non-zero lamport balance. Because the System program's `Transfer` instruction lets *any* unprivileged signer send lamports to *any* pubkey with no ownership or existence checks on the destination, an attacker can pre-fund a target address (e.g. a PDA a protocol intends to initialize later) with a single lamport, causing every future `CreateAccount` attempt at that exact address to fail forever with `SystemError::AccountAlreadyInUse`. This is the same bug class as the APWine report: a balance-derived precondition (`balance == 0` here, vs. `balanceOf()`-derived redemption amount there) is corrupted by an attacker gratuitously sending funds to the target, permanently blocking the legitimate operation tied to that account/address.

### Finding Description
`create_account()` in the System builtin program enforces: [1](#0-0) 

```
    // if it looks like the `to` account is already in use, bail
    {
        let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
        if to.get_lamports() > 0 {
            ic_msg!(...);
            return Err(SystemError::AccountAlreadyInUse.into());
        }
        allocate_and_assign(&mut to, to_address, space, owner, signers, invoke_context)?;
    }
    transfer(...)
```

This check assumes an account that has never been "created" will have exactly zero lamports. That assumption does not hold on Solana: `system_processor::transfer_verified()` moves lamports into *any* destination account regardless of its owner, data, or existence — there is no requirement that the destination be uninitialized, owned by System, or have prior activity: [2](#0-1) 

An unprivileged attacker who knows (or can derive/front-run) the address a protocol is about to `CreateAccount` at — a common pattern for deterministic PDAs used as vaults, pools, escrow accounts, or per-user state accounts — can send it 1 lamport via an ordinary `Transfer` instruction before the legitimate `CreateAccount` transaction lands. From that point forward, `to.get_lamports() > 0` is always true, and the intended `CreateAccount` at that exact address will fail with `AccountAlreadyInUse` indefinitely; there is no way for the legitimate owner to "reset" the balance back to zero without a privileged sweep (lamports cannot be burned/removed except by transferring them out, which requires the account to already be owned by a program that permits it).

Notably, the codebase itself contains an alternate code path, `create_account_allow_prefund()`, explicitly documented as "Create a new account without checking for 0 lamports... intended for use where account has already had rent paid... before creation": [3](#0-2) 

This confirms the balance-based `> 0` precondition in ordinary `create_account()` is a known footgun that the runtime works around internally in certain rent/fee-prepayment flows, but this permissive variant is not what is invoked for the general, user-facing `SystemInstruction::CreateAccount` path exposed to CPI callers/programs.

### Impact Explanation
This is functionally identical in shape to the APWine finding: an attacker spends a trivial amount (here, 1 lamport plus a transaction fee) to corrupt a balance precondition on a specific account/address, causing a legitimate, expected operation on that account to be permanently blocked. Any program or protocol that relies on `CreateAccount` (directly or via CPI) to lazily initialize a deterministic account (e.g., a PDA-based vault, pool, or per-market state account) can have that specific address permanently "squatted," freezing all funds or logic that were supposed to be housed at that address and preventing the protocol from ever initializing it as intended. This is a real, low-cost, permanent denial-of-service against on-chain protocols built on top of the System program's `CreateAccount` instruction.

### Likelihood Explanation
High. Anyone can construct the destination pubkey ahead of time if it is derived deterministically (a very common pattern for PDAs, and even for plain keypair addresses that are publicly announced before initialization, e.g. token mint addresses, vault addresses in deployment scripts). Sending lamports via `Transfer` requires no special privilege, no CPI, and no cooperation from the target program. The cost is a single lamport and one transaction fee, and the attack can be repeated cheaply across many target addresses (this mirrors the report's note that the attacker can perform the exploit "cheaply for every market").

### Recommendation
Change the "already in use" check in `create_account()` to not depend solely on `lamports() > 0`. Instead, gate account creation on whether the account is *already initialized* (i.e., has non-empty data or is owned by a non-System program), matching the check already used by `allocate()`: [4](#0-3) 

and use the `create_account_allow_prefund` semantics (fold any pre-existing dust lamports into the new account rather than rejecting outright) as the default behavior for `CreateAccount`, so that gratuitous lamport transfers to a not-yet-created address cannot permanently block its creation.

### Proof of Concept
1. Legitimate protocol computes a deterministic target address `T` for a PDA/vault that it plans to create later via `SystemInstruction::CreateAccount { lamports, space, owner }`.
2. Attacker, an unprivileged signer, submits `system_instruction::transfer(attacker, T, 1)` before the protocol's creation transaction lands. This succeeds unconditionally per `transfer_verified()`.
3. Protocol later submits its intended `CreateAccount` instruction targeting `T`.
4. `system_processor::create_account()` observes `to.get_lamports() == 1 > 0` and returns `SystemError::AccountAlreadyInUse`, permanently failing the instruction. The attacker can repeat step 2 immediately after any retry, since `T`'s balance never returns to zero without a privileged withdrawal path that does not exist for a System-owned, data-empty account with no assigned authority.

### Citations

**File:** programs/system/src/system_processor.rs (L91-100)
```rust
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

**File:** programs/system/src/system_processor.rs (L161-174)
```rust
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

**File:** programs/system/src/system_processor.rs (L216-243)
```rust
fn transfer_verified(
    from_account_index: IndexOfAccount,
    to_account_index: IndexOfAccount,
    lamports: u64,
    invoke_context: &InvokeContext,
    instruction_context: &InstructionContext,
) -> Result<(), InstructionError> {
    let mut from = instruction_context.try_borrow_instruction_account(from_account_index)?;
    if !from.get_data().is_empty() {
        ic_msg!(invoke_context, "Transfer: `from` must not carry data");
        return Err(InstructionError::InvalidArgument);
    }
    if lamports > from.get_lamports() {
        ic_msg!(
            invoke_context,
            "Transfer: insufficient lamports {}, need {}",
            from.get_lamports(),
            lamports
        );
        return Err(SystemError::ResultWithNegativeLamports.into());
    }

    from.checked_sub_lamports(lamports)?;
    drop(from);
    let mut to = instruction_context.try_borrow_instruction_account(to_account_index)?;
    to.checked_add_lamports(lamports)?;
    Ok(())
}
```
