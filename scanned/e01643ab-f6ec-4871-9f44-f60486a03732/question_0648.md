# Q648: address_lookup_table::load_addresses - duplicate resolved addresses defeat account locks (pointing two lookup indexes at the)

## Question
Can an unprivileged attacker who submits a v0 transaction with address-table lookups pointing at tables the attacker created or extended, pointing two lookup indexes at the same underlying address, drive `address_lookup_table::load_addresses` to resolve the same pubkey through two lookup indexes so it is locked and accounted twice, so that the invariant that the resolved account list contains no duplicates and matches the lock set is broken and the outcome is Liveness/Loss of Availability (cluster halt requiring human intervention)?

## Target
- File/function: `runtime/src/bank/address_lookup_table.rs` -> `load_addresses`
- Entrypoint: submits a v0 transaction with address-table lookups pointing at tables the attacker created or extended, pointing two lookup indexes at the same underlying address
- Attacker controls: the lookup table account contents, its authority, the writable/readonly index vectors, and the slot at which the table was last extended
- Exploit idea: Resolve the same pubkey through two lookup indexes so it is locked and accounted twice.
- Invariant to test: The resolved account list contains no duplicates and matches the lock set.
- Expected Immunefi impact: Critical - Liveness/Loss of Availability (cluster halt requiring human intervention)
- Fast validation: bank integration test resolving the crafted lookup and asserting the resolved address set and privileges match the signed message
