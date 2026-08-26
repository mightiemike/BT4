# Q1516: program_loader::load_program_with_pubkey - delay visibility bypassed (closing the program in the same)

## Question
Can an unprivileged attacker who deploys, upgrades or closes its own program and invokes it in nearby slots, closing the program in the same block in which another of its transactions invokes it, drive `program_loader::load_program_with_pubkey` to invoke a program in the same slot it was deployed or upgraded so the new bytecode executes immediately, so that the invariant that newly deployed bytecode is only executable from the slot after deployment is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `svm/src/program_loader.rs` -> `load_program_with_pubkey`
- Entrypoint: deploys, upgrades or closes its own program and invokes it in nearby slots, closing the program in the same block in which another of its transactions invokes it
- Attacker controls: the program and programdata account contents, the deployment slot, and when the invoking transaction lands
- Exploit idea: Invoke a program in the same slot it was deployed or upgraded so the new bytecode executes immediately.
- Invariant to test: Newly deployed bytecode is only executable from the slot after deployment.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: SVM unit test loading the crafted program accounts and asserting the effective deployment slot and visibility rules hold
