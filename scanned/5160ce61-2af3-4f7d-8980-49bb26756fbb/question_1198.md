# Q1198: SVM slot checkpoint - retry window skip window

## Question
When an unprivileged actor submit many public SVM gateway transactions so the listener scans a large slot range, does `updateLastProcessedSlot` remain safe if they control the exact slot timing during restart, re-scan, and confirmation retries, or can that make it skip a real user event during slot progression so funds stay permanently unobserved or unrefunded, violate the rule that restart and resume logic never reclassifies or duplicates already-seen SVM traffic, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/svm/event_listener.go:updateLastProcessedSlot
- Entrypoint: submit many public SVM gateway transactions so the listener scans a large slot range
- Attacker controls: the exact slot timing during restart, re-scan, and confirmation retries
- Exploit idea: skip a real user event during slot progression so funds stay permanently unobserved or unrefunded
- Invariant to test: restart and resume logic never reclassifies or duplicates already-seen SVM traffic
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: force events into both fast and standard paths and confirm the same economic flow cannot switch thresholds under attacker control
