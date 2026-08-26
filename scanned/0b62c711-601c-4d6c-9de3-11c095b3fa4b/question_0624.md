# Q624: address_lookup_table::load_addresses_from_ref - table account not owned by the address lookup table program

## Question
Can an unprivileged attacker who submits a v0 transaction with address-table lookups pointing at tables the attacker created or extended, extending the lookup table in the slot between signing and execution, drive `address_lookup_table::load_addresses_from_ref` to point a lookup at an attacker-owned account whose bytes deserialize as a table, so that the invariant that only accounts owned by the address lookup table program can be used for resolution is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `runtime/src/bank/address_lookup_table.rs` -> `load_addresses_from_ref`
- Entrypoint: submits a v0 transaction with address-table lookups pointing at tables the attacker created or extended, extending the lookup table in the slot between signing and execution
- Attacker controls: the lookup table account contents, its authority, the writable/readonly index vectors, and the slot at which the table was last extended
- Exploit idea: Point a lookup at an attacker-owned account whose bytes deserialize as a table.
- Invariant to test: Only accounts owned by the address lookup table program can be used for resolution.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: bank integration test resolving the crafted lookup and asserting the resolved address set and privileges match the signed message
