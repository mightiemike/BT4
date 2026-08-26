# Q4113: system_processor::assign - assign moves an account to a program the signer did not authorize

## Question
Can an unprivileged attacker who submits a transaction invoking the system program directly or via CPI from its own program, invoking the system program through CPI from its own deployed program, drive `system_processor::assign` to reassign ownership of an account whose current owner or signer did not approve, so that the invariant that ownership changes require the account's signature and system ownership is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/system/src/system_processor.rs` -> `assign`
- Entrypoint: submits a transaction invoking the system program directly or via CPI from its own program, invoking the system program through CPI from its own deployed program
- Attacker controls: the system instruction variant, seeds, space, lamports, owner and which accounts sign
- Exploit idea: Reassign ownership of an account whose current owner or signer did not approve.
- Invariant to test: Ownership changes require the account's signature and system ownership.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the system instruction against the crafted accounts and assert the signer and ownership checks reject it
