# Q4205: system_processor::create_account_allow_prefund - rent-exempt minimum not enforced on creation (targeting an account that another instruction)

## Question
Can an unprivileged attacker who submits a transaction invoking the system program directly or via CPI from its own program, targeting an account that another instruction in the same transaction just closed, drive `system_processor::create_account_allow_prefund` to create an account funded below the rent-exempt minimum, so that the invariant that newly created accounts are rent-exempt is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `programs/system/src/system_processor.rs` -> `create_account_allow_prefund`
- Entrypoint: submits a transaction invoking the system program directly or via CPI from its own program, targeting an account that another instruction in the same transaction just closed
- Attacker controls: the system instruction variant, seeds, space, lamports, owner and which accounts sign
- Exploit idea: Create an account funded below the rent-exempt minimum.
- Invariant to test: Newly created accounts are rent-exempt.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: unit-test the system instruction against the crafted accounts and assert the signer and ownership checks reject it
