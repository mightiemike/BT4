# Q1504: program_loader::load_program_with_pubkey - program loaded from mismatched program/programdata pair (listing the programdata account as writable)

## Question
Can an unprivileged attacker who deploys, upgrades or closes its own program and invokes it in nearby slots, listing the programdata account as writable in the invoking transaction, drive `program_loader::load_program_with_pubkey` to make load_program_accounts pair a program account with programdata that does not belong to it, so that the invariant that a program's executable bytes come only from its own programdata account is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `svm/src/program_loader.rs` -> `load_program_with_pubkey`
- Entrypoint: deploys, upgrades or closes its own program and invokes it in nearby slots, listing the programdata account as writable in the invoking transaction
- Attacker controls: the program and programdata account contents, the deployment slot, and when the invoking transaction lands
- Exploit idea: Make load_program_accounts pair a program account with programdata that does not belong to it.
- Invariant to test: A program's executable bytes come only from its own programdata account.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: SVM unit test loading the crafted program accounts and asserting the effective deployment slot and visibility rules hold
