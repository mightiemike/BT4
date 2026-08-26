# Q5978: sysvar_account::from_account - sysvar deserialization silently yields a default (transferring lamports into the sysvar account)

## Question
Can an unprivileged attacker who submits transactions that reference sysvar accounts and observe their contents, transferring lamports into the sysvar account first, drive `sysvar_account::from_account` to make from_account return a default value for corrupted sysvar bytes, so that the invariant that corrupted sysvar data produces an error, never a default is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/sysvar_account.rs` -> `from_account`
- Entrypoint: submits transactions that reference sysvar accounts and observe their contents, transferring lamports into the sysvar account first
- Attacker controls: which sysvar accounts are listed in the transaction and whether they are marked writable
- Exploit idea: Make from_account return a default value for corrupted sysvar bytes.
- Invariant to test: Corrupted sysvar data produces an error, never a default.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: bank test comparing the sysvar account's stored bytes and fields against the bank's live values
