# Q5987: sysvar_account::to_account - sysvar account owner or fields set incorrectly (transferring lamports into the sysvar account)

## Question
Can an unprivileged attacker who submits transactions that reference sysvar accounts and observe their contents, transferring lamports into the sysvar account first, drive `sysvar_account::to_account` to make create_account produce a sysvar account with a non-sysvar owner or wrong flags, so that the invariant that sysvar accounts always carry the sysvar owner and protocol-mandated fields is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/sysvar_account.rs` -> `to_account`
- Entrypoint: submits transactions that reference sysvar accounts and observe their contents, transferring lamports into the sysvar account first
- Attacker controls: which sysvar accounts are listed in the transaction and whether they are marked writable
- Exploit idea: Make create_account produce a sysvar account with a non-sysvar owner or wrong flags.
- Invariant to test: Sysvar accounts always carry the sysvar owner and protocol-mandated fields.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: bank test comparing the sysvar account's stored bytes and fields against the bank's live values
