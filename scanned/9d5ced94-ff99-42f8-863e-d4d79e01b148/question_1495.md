# Q1495: program_loader::load_program_with_pubkey - executable filter admits a non-program account

## Question
Can an unprivileged attacker who deploys, upgrades or closes its own program and invokes it in nearby slots, upgrading the program in the slot immediately before the invoking transaction lands, drive `program_loader::load_program_with_pubkey` to get filter_executable_program_accounts to include an account that is not a deployed program, so that the invariant that only accounts owned by a loader and marked executable are loaded as programs is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `svm/src/program_loader.rs` -> `load_program_with_pubkey`
- Entrypoint: deploys, upgrades or closes its own program and invokes it in nearby slots, upgrading the program in the slot immediately before the invoking transaction lands
- Attacker controls: the program and programdata account contents, the deployment slot, and when the invoking transaction lands
- Exploit idea: Get filter_executable_program_accounts to include an account that is not a deployed program.
- Invariant to test: Only accounts owned by a loader and marked executable are loaded as programs.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: SVM unit test loading the crafted program accounts and asserting the effective deployment slot and visibility rules hold
