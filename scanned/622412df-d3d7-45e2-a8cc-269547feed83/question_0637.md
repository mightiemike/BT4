# Q637: address_lookup_table::load_addresses - deactivated table still resolvable (pointing two lookup indexes at the)

## Question
Can an unprivileged attacker who submits a v0 transaction with address-table lookups pointing at tables the attacker created or extended, pointing two lookup indexes at the same underlying address, drive `address_lookup_table::load_addresses` to resolve through a table that has been deactivated or closed so a freed account slot is reused, so that the invariant that a deactivated lookup table cannot be used to resolve addresses is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/bank/address_lookup_table.rs` -> `load_addresses`
- Entrypoint: submits a v0 transaction with address-table lookups pointing at tables the attacker created or extended, pointing two lookup indexes at the same underlying address
- Attacker controls: the lookup table account contents, its authority, the writable/readonly index vectors, and the slot at which the table was last extended
- Exploit idea: Resolve through a table that has been deactivated or closed so a freed account slot is reused.
- Invariant to test: A deactivated lookup table cannot be used to resolve addresses.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: bank integration test resolving the crafted lookup and asserting the resolved address set and privileges match the signed message
