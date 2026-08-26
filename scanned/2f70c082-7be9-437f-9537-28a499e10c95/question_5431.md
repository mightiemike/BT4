# Q5431: recent_blockhashes_account::update_account - fee rate in the sysvar differs from the charged rate (landing during a period where the)

## Question
Can an unprivileged attacker who submits transactions that read the recent blockhashes sysvar or rely on its fee rates, landing during a period where the fee rate has just changed, drive `recent_blockhashes_account::update_account` to publish a lamports_per_signature in the sysvar that differs from what fees use, so that the invariant that the sysvar fee rate equals the rate used to charge transactions is broken and the outcome is Loss of Funds (fee, rent or reward undercharge draining protocol economics)?

## Target
- File/function: `runtime/src/bank/recent_blockhashes_account.rs` -> `update_account`
- Entrypoint: submits transactions that read the recent blockhashes sysvar or rely on its fee rates, landing during a period where the fee rate has just changed
- Attacker controls: the timing of submission relative to sysvar updates and which blockhash the transaction carries
- Exploit idea: Publish a lamports_per_signature in the sysvar that differs from what fees use.
- Invariant to test: The sysvar fee rate equals the rate used to charge transactions.
- Expected Immunefi impact: High - Loss of Funds (fee, rent or reward undercharge draining protocol economics)
- Fast validation: bank test comparing the sysvar account contents against the blockhash queue and asserting they match exactly
