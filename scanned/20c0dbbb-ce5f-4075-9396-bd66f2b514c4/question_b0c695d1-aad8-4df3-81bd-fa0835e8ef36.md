[File: program-runtime/src/invoke_context.rs -> Scope: Critical] [Function: InvokeContext::prepare_top_level_instructions vs prepare_next_cpi_instruction divergence] Can an attacker exploit the fact that prepare_top_level_instructions computes privileges directly from the SanitizedMessage's compiled account metas (already deduplicated/signed by sigverify) while prepare_next_cpi_instruction recomputes privileges dynamically from instruction_accounts state, such that a top-level instruction's OR-merged duplicate privilege differs from what a CPI back into the same accounts would compute, letting a CPI 'reset' an account's writable status to a narrower one and then a SIBLING top-level instruction (already prepared) retain the wider privilege inconsistently across the transaction, causing state-dependent double standards in privilege enforcement? Preconditions: multi-

### Citations

**File:** program-runtime/src/mem_pool.rs (L17-55)
```rust
struct Pool<T: Reset, const SIZE: usize> {
    items: [Option<T>; SIZE],
    next_empty: usize,
}

impl<T: Reset, const SIZE: usize> Pool<T, SIZE> {
    fn new(items: [T; SIZE]) -> Self {
        Self {
            items: items.map(|i| Some(i)),
            next_empty: SIZE,
        }
    }

    fn len(&self) -> usize {
        SIZE
    }

    fn get(&mut self) -> Option<T> {
        if self.next_empty == 0 {
            return None;
        }
        self.next_empty = self.next_empty.saturating_sub(1);
        self.items
            .get_mut(self.next_empty)
            .and_then(|item| item.take())
    }

    fn put(&mut self, mut value: T) -> bool {
        self.items
            .get_mut(self.next_empty)
            .map(|item| {
                value.reset();
                item.replace(value);
                self.next_empty = self.next_empty.saturating_add(1);
                true
            })
            .unwrap_or(false)
    }
}
```

**File:** program-runtime/src/serialization.rs (L556-571)
```rust
    s.write::<u64>((accounts.len() as u64).to_le());
    for account in accounts {
        match account {
            SerializeAccount::Account(_, mut borrowed_account) => {
                let vm_addr = s.write::<u8>(NON_DUP_MARKER);
                s.write::<u8>(borrowed_account.is_signer() as u8);
                s.write::<u8>(borrowed_account.is_writable() as u8);
                #[expect(deprecated)]
                s.write::<u8>(borrowed_account.is_executable() as u8);
                s.write_all(&[0u8, 0, 0, 0]);
                let vm_key_addr = s.write_all(borrowed_account.get_key().as_ref());
                let vm_owner_addr = s.write_all(borrowed_account.get_owner().as_ref());
                let vm_lamports_addr = s.write::<u64>(borrowed_account.get_lamports().to_le());
                s.write::<u64>((borrowed_account.get_data().len() as u64).to_le());
                let vm_data_addr = s.write_account(&mut borrowed_account)?;
                let rent_epoch = u64::MAX;
```

**File:** program-runtime/src/invoke_context.rs (L410-459)
```rust
            for current_index in 0..instruction_accounts.len() {
                let instruction_account = instruction_accounts.get(current_index).unwrap();
                let index_in_callee = *transaction_callee_map
                    .get(instruction_account.index_in_transaction as usize)
                    .unwrap() as usize;

                if current_index != index_in_callee {
                    let (is_signer, is_writable) = {
                        let reference_account = instruction_accounts
                            .get(index_in_callee)
                            .ok_or(InstructionError::MissingAccount)?;
                        (
                            reference_account.is_signer(),
                            reference_account.is_writable(),
                        )
                    };

                    let current_account = instruction_accounts.get_mut(current_index).unwrap();
                    current_account.set_is_signer(current_account.is_signer() || is_signer);
                    current_account.set_is_writable(current_account.is_writable() || is_writable);
                    // This account is repeated, so there is no need to check for permissions
                    continue;
                }

                let index_in_caller = instruction_context.get_index_of_account_in_instruction(
                    instruction_account.index_in_transaction,
                )?;

                // This unwrap is safe because instruction.accounts.len() == instruction_accounts.len()
                let account_key = &instruction.accounts.get(current_index).unwrap().pubkey;
                // get_index_of_account_in_instruction has already checked if the index is valid.
                let caller_instruction_account = instruction_context
                    .instruction_accounts()
                    .get(index_in_caller as usize)
                    .unwrap();

                // Readonly in caller cannot become writable in callee
                if instruction_account.is_writable() && !caller_instruction_account.is_writable() {
                    ic_msg!(self,
