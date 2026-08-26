# Q4182: system_processor::create - prefunded account creation overwrites live state (targeting an account that another instruction)

## Question
Can an unprivileged attacker who submits a transaction invoking the system program directly or via CPI from its own program, targeting an account that another instruction in the same transaction just closed, drive `system_processor::create` to use create_account_allow_prefund on an address that already holds data or a non-system owner, so that the invariant that creation never overwrites an account that already has data or a non-system owner is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/system/src/system_processor.rs` -> `create`
- Entrypoint: submits a transaction invoking the system program directly or via CPI from its own program, targeting an account that another instruction in the same transaction just closed
- Attacker controls: the system instruction variant, seeds, space, lamports, owner and which accounts sign
- Exploit idea: Use create_account_allow_prefund on an address that already holds data or a non-system owner.
- Invariant to test: Creation never overwrites an account that already has data or a non-system owner.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the system instruction against the crafted accounts and assert the signer and ownership checks reject it
