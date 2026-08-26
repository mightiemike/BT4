# Q4122: system_processor::create_account - creation of an account at a PDA the caller cannot derive

## Question
Can an unprivileged attacker who submits a transaction invoking the system program directly or via CPI from its own program, invoking the system program through CPI from its own deployed program, drive `system_processor::create_account` to create an account at an address that a different program's PDA space owns, so that the invariant that PDA-addressed accounts can only be created by the deriving program is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/system/src/system_processor.rs` -> `create_account`
- Entrypoint: submits a transaction invoking the system program directly or via CPI from its own program, invoking the system program through CPI from its own deployed program
- Attacker controls: the system instruction variant, seeds, space, lamports, owner and which accounts sign
- Exploit idea: Create an account at an address that a different program's PDA space owns.
- Invariant to test: PDA-addressed accounts can only be created by the deriving program.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the system instruction against the crafted accounts and assert the signer and ownership checks reject it
