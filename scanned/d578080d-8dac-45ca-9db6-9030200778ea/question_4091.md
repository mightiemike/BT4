# Q4091: system_processor::assign - account created or assigned without its own signature

## Question
Can an unprivileged attacker who submits a transaction invoking the system program directly or via CPI from its own program, invoking the system program through CPI from its own deployed program, drive `system_processor::assign` to create, allocate or assign an account whose key never signed the transaction, so that the invariant that system operations on an address require that address's signature or a valid PDA derivation is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/system/src/system_processor.rs` -> `assign`
- Entrypoint: submits a transaction invoking the system program directly or via CPI from its own program, invoking the system program through CPI from its own deployed program
- Attacker controls: the system instruction variant, seeds, space, lamports, owner and which accounts sign
- Exploit idea: Create, allocate or assign an account whose key never signed the transaction.
- Invariant to test: System operations on an address require that address's signature or a valid PDA derivation.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test the system instruction against the crafted accounts and assert the signer and ownership checks reject it
