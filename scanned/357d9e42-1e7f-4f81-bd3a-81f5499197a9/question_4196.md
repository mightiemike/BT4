# Q4196: system_processor::transfer - signer check reads the wrong instruction account (targeting an account that another instruction)

## Question
Can an unprivileged attacker who submits a transaction invoking the system program directly or via CPI from its own program, targeting an account that another instruction in the same transaction just closed, drive `system_processor::transfer` to make is_signer consult an account other than the one being operated on, so that the invariant that the signer check applies to the account being modified is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/system/src/system_processor.rs` -> `transfer`
- Entrypoint: submits a transaction invoking the system program directly or via CPI from its own program, targeting an account that another instruction in the same transaction just closed
- Attacker controls: the system instruction variant, seeds, space, lamports, owner and which accounts sign
- Exploit idea: Make is_signer consult an account other than the one being operated on.
- Invariant to test: The signer check applies to the account being modified.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the system instruction against the crafted accounts and assert the signer and ownership checks reject it
