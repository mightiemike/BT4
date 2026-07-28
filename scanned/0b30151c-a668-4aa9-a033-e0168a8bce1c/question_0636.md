# Q0636: SVM confirm selection - batch contents wrong confirm path

## Question
If a user submit many public SVM gateway transactions so the listener scans a large slot range, can `getRequiredConfirmations` be pushed into a path where signature batch size, repeated signatures, and ordering of parsed logs within one batch causes it to apply the wrong confirmation threshold so a weakly finalized event reaches voting or a real event never does, so that restart and resume logic never reclassifies or duplicates already-seen SVM traffic no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_confirmer.go:getRequiredConfirmations
- Entrypoint: submit many public SVM gateway transactions so the listener scans a large slot range
- Attacker controls: signature batch size, repeated signatures, and ordering of parsed logs within one batch
- Exploit idea: apply the wrong confirmation threshold so a weakly finalized event reaches voting or a real event never does
- Invariant to test: restart and resume logic never reclassifies or duplicates already-seen SVM traffic
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force events into both fast and standard paths and confirm the same economic flow cannot switch thresholds under attacker control
