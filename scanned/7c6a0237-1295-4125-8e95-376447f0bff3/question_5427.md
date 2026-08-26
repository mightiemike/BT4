# Q5427: recent_blockhashes_account::update_account - sysvar update observed mid-block by a program (reading the sysvar through both the)

## Question
Can an unprivileged attacker who submits transactions that read the recent blockhashes sysvar or rely on its fee rates, reading the sysvar through both the syscall and an explicitly passed account, drive `recent_blockhashes_account::update_account` to read the sysvar from two transactions in one block and observe different contents, so that the invariant that sysvar contents are constant for the whole slot is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `runtime/src/bank/recent_blockhashes_account.rs` -> `update_account`
- Entrypoint: submits transactions that read the recent blockhashes sysvar or rely on its fee rates, reading the sysvar through both the syscall and an explicitly passed account
- Attacker controls: the timing of submission relative to sysvar updates and which blockhash the transaction carries
- Exploit idea: Read the sysvar from two transactions in one block and observe different contents.
- Invariant to test: Sysvar contents are constant for the whole slot.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: bank test comparing the sysvar account contents against the blockhash queue and asserting they match exactly
