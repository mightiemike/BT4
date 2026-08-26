# Q617: address_lookup_table::load_addresses - addresses added after the transaction was signed

## Question
Can an unprivileged attacker who submits a v0 transaction with address-table lookups pointing at tables the attacker created or extended, extending the lookup table in the slot between signing and execution, drive `address_lookup_table::load_addresses` to extend the table between signing and execution so the transaction resolves to accounts the signer never saw, so that the invariant that resolution only uses addresses that were active at or before the transaction's blockhash slot is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `runtime/src/bank/address_lookup_table.rs` -> `load_addresses`
- Entrypoint: submits a v0 transaction with address-table lookups pointing at tables the attacker created or extended, extending the lookup table in the slot between signing and execution
- Attacker controls: the lookup table account contents, its authority, the writable/readonly index vectors, and the slot at which the table was last extended
- Exploit idea: Extend the table between signing and execution so the transaction resolves to accounts the signer never saw.
- Invariant to test: Resolution only uses addresses that were active at or before the transaction's blockhash slot.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: bank integration test resolving the crafted lookup and asserting the resolved address set and privileges match the signed message
