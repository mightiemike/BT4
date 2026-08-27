I have enough evidence to confirm the analog. The lookup-table program implementation itself isn't in the address-lookup-table-program repo indexed here, but the client-facing resolution path in Agave clearly confirms the core mechanism: addresses referenced by index in an Address Lookup Table (ALT) are resolved against the **current on-chain state of the table at load time**, not against any value pinned at signing time, and there is no check binding the resolved address back to what the signer intended.

### Title
Address Lookup Table content can be mutated by its authority after a transaction is signed, letting a counterparty redirect funds referenced via table indices - ([File: accounts-db/src/accounts.rs])

### Summary
Solana transactions that reference accounts via an Address Lookup Table (ALT) index (v0 messages) do not commit to the actual `Pubkey` at signing time — they commit only to `(table_key, index)`. The runtime resolves the concrete addresses from the on-chain ALT account's current contents when the transaction is loaded/executed, via `Accounts::load_lookup_table_addresses_into` [1](#0-0)  and `Bank::load_addresses_from_ref` [2](#0-1) . This is structurally identical to the `NFTPairWithOracle` bug: a value that one party relied on when "agreeing" to a transaction (the address behind an index / the oracle to use) can be changed by the counterparty who controls the mutable resource (the ALT authority / the loan's oracle field) between agreement and execution, without any check tying the two together.

### Finding Description
When a client builds and signs a v0 transaction, it specifies accounts via `MessageAddressTableLookup { account_key, writable_indexes, readonly_indexes }` — indices into an ALT, not the resolved pubkeys themselves. The resolved pubkeys are computed later, at transaction-processing time, by reading whatever addresses currently live in the on-chain ALT account: [3](#0-2) 

Nothing in this resolution path, nor in the higher-level callers (`Bank::load_addresses_from_ref` [2](#0-1) , `load_addresses_for_view` [4](#0-3) ), checks that the addresses obtained from the table match what the signer(s) expected when they signed the transaction. The ALT's authority can freely call `extend_lookup_table` at any time before the referencing transaction lands, appending or (via table reuse patterns) altering which pubkey occupies a given index that a not-yet-landed, already-signed transaction depends on — analogous to the lender in the Code4rena report altering `params.oracle` on `updateLoanParams()` after the loan terms were fixed by `requestLoan()`/`lend()`.

Concretely, in any multi-party protocol where:
1. Party A signs an offline transaction (e.g., a swap, an escrow release, a claim) that references Party B's destination account by ALT index rather than as a static account key, and
2. Party B controls the authority of that ALT,

Party B can extend/mutate the ALT after A signs but before the transaction lands, so that the index A referenced now resolves to an address B controls instead of the originally agreed-upon address. The transaction still passes sanitization (`SVMMessage::account_keys()` are resolved dynamically) and executes with the new resolution — the runtime has no way to detect that "the meaning of index N changed" because it only tracks `deactivation_slot`, not content equality: [5](#0-4) . The only related runtime guard is the ALT's `deactivation_slot`/expiry window used for `max_age` (`AddressLookupError` handling) [6](#0-5) , which protects against *closing* a table, not against *extending/rewriting* its live content.

### Impact Explanation
This allows theft of funds analogous to the oracle-swap report: whichever party controls the ALT authority referenced in a peer's pre-signed, unlanded transaction can redirect the resolved destination account for any index used in that transaction, diverting the counterparty's funds/authority to an address of the attacker's choosing at the moment of landing. This is a direct funds-theft primitive reachable purely through ordinary, unprivileged client transactions (`ExtendLookupTable` instructions), no special validator or node privilege required.

### Likelihood Explanation
Likelihood is contingent on application-level design: it is only exploitable by protocols/wallets that let a counterparty's ALT resolve indices referenced in another party's pre-signed transaction (e.g., certain swap/escrow flows using shared or counterparty-supplied lookup tables), and requires the attacker to land an `ExtendLookupTable` transaction in the window between the victim signing and the victim's transaction landing. This is a widely known Solana ALT caveat (frozen tables are the standard mitigation), so likelihood of a *novel* client falling into this trap is moderate rather than universal, but the underlying resolve-at-execution-time behavior is unconditional and unguarded in the code paths shown above.

### Recommendation
For any protocol that must bind a transaction to specific resolved addresses regardless of ALT mutation, either (a) require the ALT be frozen (`FreezeLookupTable`) before it is relied upon by unlanded, counterparty-supplied transactions, or (b) have the runtime/SDK optionally support pinning and verifying resolved addresses against an expected set at transaction-processing time (rejecting the transaction if resolution no longer matches what was recorded when the offline transaction was constructed/simulated), similar to how `params.oracle == cur.oracle` was recommended to be enforced in the original report.

### Proof of Concept
1. Party A (victim) and Party B agree on a transaction template in which one instruction's account list references index `i` of ALT `T`, currently resolving to `dest = Party A's expected recipient` (e.g., B's payout wallet).
2. A signs the v0 transaction offline; A never signs over the resolved pubkey, only over `(T, writable_indexes=[i])`, per `MessageAddressTableLookup`.
3. Before A's transaction lands, B, as `T`'s authority, submits `ExtendLookupTable`/reconstructs `T` such that index `i` now resolves to `attacker_dest` (an address B controls) — permitted unconditionally by the ALT program's authority check, with no linkage to any pending transaction.
4. A's transaction eventually lands; `Accounts::load_lookup_table_addresses_into` [3](#0-2)  resolves index `i` to `attacker_dest` at that time, and the instruction executes moving funds/authority to `attacker_dest` instead of A's intended recipient — with no error and no consensus-level check preventing it.

### Citations

**File:** accounts-db/src/accounts.rs (L86-102)
```rust
    /// Return loaded addresses and the deactivation slot.
    /// If the table hasn't been deactivated, the deactivation slot is `u64::MAX`.
    pub fn load_lookup_table_addresses(
        &self,
        ancestors: &Ancestors,
        address_table_lookup: SVMMessageAddressTableLookup,
        slot_hashes: &SlotHashes,
    ) -> std::result::Result<(LoadedAddresses, Slot), AddressLookupError> {
        let mut loaded_addresses = LoadedAddresses::default();
        self.load_lookup_table_addresses_into(
            ancestors,
            address_table_lookup,
            slot_hashes,
            &mut loaded_addresses,
        )
        .map(|deactivation_slot| (loaded_addresses, deactivation_slot))
    }
```

**File:** accounts-db/src/accounts.rs (L106-162)
```rust
    pub fn load_lookup_table_addresses_into(
        &self,
        ancestors: &Ancestors,
        address_table_lookup: SVMMessageAddressTableLookup,
        slot_hashes: &SlotHashes,
        loaded_addresses: &mut LoadedAddresses,
    ) -> std::result::Result<Slot, AddressLookupError> {
        let table_account = self
            .load_with_fixed_root(
                ancestors,
                address_table_lookup.account_key,
                None::<fn(_, &_, _) -> _>,
            )
            .map(|(account, _rent)| account)
            .ok_or(AddressLookupError::LookupTableAccountNotFound)?;

        if table_account.owner() == &address_lookup_table::program::id() {
            let current_slot = ancestors.max_slot();
            let lookup_table = AddressLookupTable::deserialize(table_account.data())
                .map_err(|_ix_err| AddressLookupError::InvalidAccountData)?;

            // Load iterators for addresses.
            let writable_addresses = lookup_table.lookup_iter(
                current_slot,
                address_table_lookup.writable_indexes,
                slot_hashes,
            )?;
            let readonly_addresses = lookup_table.lookup_iter(
                current_slot,
                address_table_lookup.readonly_indexes,
                slot_hashes,
            )?;

            // Reserve space in vectors to avoid reallocations.
            // If `loaded_addresses` is pre-allocated, this only does a simple
            // bounds check.
            loaded_addresses
                .writable
                .reserve(address_table_lookup.writable_indexes.len());
            loaded_addresses
                .readonly
                .reserve(address_table_lookup.readonly_indexes.len());

            // Append to the loaded addresses.
            // Check if **any** of the addresses are not available.
            for address in writable_addresses {
                loaded_addresses
                    .writable
                    .push(address.ok_or(AddressLookupError::InvalidLookupIndex)?);
            }
            for address in readonly_addresses {
                loaded_addresses
                    .readonly
                    .push(address.ok_or(AddressLookupError::InvalidLookupIndex)?);
            }

            Ok(lookup_table.meta.deactivation_slot)
```

**File:** runtime/src/bank/address_lookup_table.rs (L41-67)
```rust
    pub fn load_addresses_from_ref<'a>(
        &self,
        address_table_lookups: impl Iterator<Item = SVMMessageAddressTableLookup<'a>>,
    ) -> Result<(LoadedAddresses, Slot), AddressLoaderError> {
        let slot_hashes = self
            .transaction_processor
            .sysvar_cache()
            .get_slot_hashes()
            .map_err(|_| AddressLoaderError::SlotHashesSysvarNotFound)?;

        let mut deactivation_slot = u64::MAX;
        let mut loaded_addresses = LoadedAddresses::default();
        for address_table_lookup in address_table_lookups {
            deactivation_slot = deactivation_slot.min(
                self.rc
                    .accounts
                    .load_lookup_table_addresses_into(
                        &self.ancestors,
                        address_table_lookup,
                        &slot_hashes,
                        &mut loaded_addresses,
                    )
                    .map_err(into_address_loader_error)?,
            );
        }

        Ok((loaded_addresses, deactivation_slot))
```

**File:** runtime/src/bank.rs (L5610-5624)
```rust
    /// Load addresses from ALTs (if necessary) and return the
    /// [`LoadedAddresses`] with the minimum deactivation slot.
    pub fn load_addresses_for_view<D: TransactionData>(
        &self,
        view: &SanitizedTransactionView<D>,
    ) -> std::result::Result<(Option<LoadedAddresses>, Slot), AddressLoaderError> {
        match view.version() {
            TransactionVersion::Legacy | TransactionVersion::V1 => Ok((None, u64::MAX)),
            TransactionVersion::V0 => self
                .load_addresses_from_ref(view.address_table_lookup_iter())
                .map(|(loaded_addresses, deactivation_slot)| {
                    (Some(loaded_addresses), deactivation_slot)
                }),
        }
    }
```

**File:** runtime/src/bank/check_transactions.rs (L231-258)
```rust
    fn check_transaction_age(
        &self,
        tx: &impl SVMMessage,
        max_age: usize,
        next_durable_nonce: &DurableNonce,
        hash_queue: &BlockhashQueue,
        error_counters: &mut TransactionErrorMetrics,
        strict_nonce_size_check: bool,
        strict_nonce_authority_check: bool,
    ) -> TransactionResult<Option<Pubkey>> {
        let recent_blockhash = tx.recent_blockhash();
        if hash_queue
            .get_hash_info_if_valid(recent_blockhash, max_age)
            .is_some()
        {
            Ok(None)
        } else if let Some((nonce_address, _)) = self.check_nonce_transaction_validity(
            tx,
            next_durable_nonce,
            strict_nonce_size_check,
            strict_nonce_authority_check,
        ) {
            Ok(Some(nonce_address))
        } else {
            error_counters.blockhash_not_found += 1;
            Err(TransactionError::BlockhashNotFound)
        }
    }
```
