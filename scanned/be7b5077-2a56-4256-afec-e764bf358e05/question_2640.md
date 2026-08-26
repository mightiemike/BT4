# Q2640: deploy::deploy_program - verification cost not charged to the deployer (deploying via CPI from another program)

## Question
Can an unprivileged attacker who deploys or upgrades its own program through a loader instruction, deploying via CPI from another program it controls, drive `deploy::deploy_program` to deploy a maximally expensive ELF while paying a fee unrelated to verification work, so that the invariant that deployment cost is proportional to the verification work performed is broken and the outcome is Liveness/Loss of Availability (block production degraded below usable capacity)?

## Target
- File/function: `program-runtime/src/deploy.rs` -> `deploy_program`
- Entrypoint: deploys or upgrades its own program through a loader instruction, deploying via CPI from another program it controls
- Attacker controls: the ELF bytes, its sections and relocations, the deployment slot and the loader used
- Exploit idea: Deploy a maximally expensive ELF while paying a fee unrelated to verification work.
- Invariant to test: Deployment cost is proportional to the verification work performed.
- Expected Immunefi impact: High - Liveness/Loss of Availability (block production degraded below usable capacity)
- Fast validation: unit-test deploy_program with the crafted ELF and assert verification rejects it before it can be cached
