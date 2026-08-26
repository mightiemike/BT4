# Q639: address_lookup_table::load_addresses - resolved address gains writable privilege it should not have (pointing two lookup indexes at the)

## Question
Can an unprivileged attacker who submits a v0 transaction with address-table lookups pointing at tables the attacker created or extended, pointing two lookup indexes at the same underlying address, drive `address_lookup_table::load_addresses` to place an address in the writable index vector that the message's privilege accounting treats as readonly downstream, so that the invariant that a resolved address's writable flag is fixed by the signed message and never widened is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `runtime/src/bank/address_lookup_table.rs` -> `load_addresses`
- Entrypoint: submits a v0 transaction with address-table lookups pointing at tables the attacker created or extended, pointing two lookup indexes at the same underlying address
- Attacker controls: the lookup table account contents, its authority, the writable/readonly index vectors, and the slot at which the table was last extended
- Exploit idea: Place an address in the writable index vector that the message's privilege accounting treats as readonly downstream.
- Invariant to test: A resolved address's writable flag is fixed by the signed message and never widened.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: bank integration test resolving the crafted lookup and asserting the resolved address set and privileges match the signed message
