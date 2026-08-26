# Q4120: system_processor::allocate - signer check reads the wrong instruction account

## Question
Can an unprivileged attacker who submits a transaction invoking the system program directly or via CPI from its own program, invoking the system program through CPI from its own deployed program, drive `system_processor::allocate` to make is_signer consult an account other than the one being operated on, so that the invariant that the signer check applies to the account being modified is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/system/src/system_processor.rs` -> `allocate`
- Entrypoint: submits a transaction invoking the system program directly or via CPI from its own program, invoking the system program through CPI from its own deployed program
- Attacker controls: the system instruction variant, seeds, space, lamports, owner and which accounts sign
- Exploit idea: Make is_signer consult an account other than the one being operated on.
- Invariant to test: The signer check applies to the account being modified.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the system instruction against the crafted accounts and assert the signer and ownership checks reject it
