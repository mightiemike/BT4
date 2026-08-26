# Q2632: deploy::deploy_program - deployment succeeds without the upgrade authority (using an ELF with overlapping or)

## Question
Can an unprivileged attacker who deploys or upgrades its own program through a loader instruction, using an ELF with overlapping or out-of-order program headers, drive `deploy::deploy_program` to deploy or replace bytecode for a program whose upgrade authority the attacker does not hold, so that the invariant that only the upgrade authority can change a program's bytecode is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/deploy.rs` -> `deploy_program`
- Entrypoint: deploys or upgrades its own program through a loader instruction, using an ELF with overlapping or out-of-order program headers
- Attacker controls: the ELF bytes, its sections and relocations, the deployment slot and the loader used
- Exploit idea: Deploy or replace bytecode for a program whose upgrade authority the attacker does not hold.
- Invariant to test: Only the upgrade authority can change a program's bytecode.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test deploy_program with the crafted ELF and assert verification rejects it before it can be cached
