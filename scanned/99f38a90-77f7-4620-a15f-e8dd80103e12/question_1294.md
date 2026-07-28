# Q1294: SVM confirm selection - retry window double observe

## Question
When an unprivileged actor submit many public SVM gateway transactions so the listener scans a large slot range, does `getRequiredConfirmations` remain safe if they control the exact slot timing during restart, re-scan, and confirmation retries, or can that make it observe the same user event twice across restart or chunk boundaries and send conflicting downstream work, violate the rule that the chosen confirmation policy matches the actual economic risk of the parsed event type, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/event_confirmer.go:getRequiredConfirmations
- Entrypoint: submit many public SVM gateway transactions so the listener scans a large slot range
- Attacker controls: the exact slot timing during restart, re-scan, and confirmation retries
- Exploit idea: observe the same user event twice across restart or chunk boundaries and send conflicting downstream work
- Invariant to test: the chosen confirmation policy matches the actual economic risk of the parsed event type
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: flood a local validator with gateway txs, then audit whether the listener preserves strict once-only processing across large slot ranges
