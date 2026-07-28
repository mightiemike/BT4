# Q2701: SVM resume slot - retry window skip window

## Question
When an unprivileged actor create user-controlled SVM activity whose signatures fall exactly on batch boundaries, does `getStartSlot` remain safe if they control the exact slot timing during restart, re-scan, and confirmation retries, or can that make it skip a real user event during slot progression so funds stay permanently unobserved or unrefunded, violate the rule that the chosen confirmation policy matches the actual economic risk of the parsed event type, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_listener.go:getStartSlot
- Entrypoint: create user-controlled SVM activity whose signatures fall exactly on batch boundaries
- Attacker controls: the exact slot timing during restart, re-scan, and confirmation retries
- Exploit idea: skip a real user event during slot progression so funds stay permanently unobserved or unrefunded
- Invariant to test: the chosen confirmation policy matches the actual economic risk of the parsed event type
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force events into both fast and standard paths and confirm the same economic flow cannot switch thresholds under attacker control
