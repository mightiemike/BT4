# Q3832: SVM confirm selection - confirmation class skip window

## Question
Can an unprivileged attacker restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state and use control over the chosen fast or standard confirmation requirement for a parsed event so that `getRequiredConfirmations` skip a real user event during slot progression so funds stay permanently unobserved or unrefunded, breaking the invariant that every finalized user-reachable SVM gateway event is scanned exactly once into the local state machine and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_confirmer.go:getRequiredConfirmations
- Entrypoint: restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state
- Attacker controls: the chosen fast or standard confirmation requirement for a parsed event
- Exploit idea: skip a real user event during slot progression so funds stay permanently unobserved or unrefunded
- Invariant to test: every finalized user-reachable SVM gateway event is scanned exactly once into the local state machine
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: feed one malformed but parseable event among many normal events and check whether later rows still advance
