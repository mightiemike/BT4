# Q2623: deploy::morph_into_deployment_environment - deployment succeeds without the upgrade authority (upgrading the program in the same)

## Question
Can an unprivileged attacker who deploys or upgrades its own program through a loader instruction, upgrading the program in the same block in which it is invoked, drive `deploy::morph_into_deployment_environment` to deploy or replace bytecode for a program whose upgrade authority the attacker does not hold, so that the invariant that only the upgrade authority can change a program's bytecode is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `program-runtime/src/deploy.rs` -> `morph_into_deployment_environment`
- Entrypoint: deploys or upgrades its own program through a loader instruction, upgrading the program in the same block in which it is invoked
- Attacker controls: the ELF bytes, its sections and relocations, the deployment slot and the loader used
- Exploit idea: Deploy or replace bytecode for a program whose upgrade authority the attacker does not hold.
- Invariant to test: Only the upgrade authority can change a program's bytecode.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test deploy_program with the crafted ELF and assert verification rejects it before it can be cached
