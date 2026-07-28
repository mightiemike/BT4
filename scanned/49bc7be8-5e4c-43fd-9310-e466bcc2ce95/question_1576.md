# Q1576: SVM confirm selection - slot cursor skip window

## Question
If a user create user-controlled SVM activity whose signatures fall exactly on batch boundaries, can `getRequiredConfirmations` be pushed into a path where start slot, last processed slot, and chunk boundaries used by the slot scanner causes it to skip a real user event during slot progression so funds stay permanently unobserved or unrefunded, so that restart and resume logic never reclassifies or duplicates already-seen SVM traffic no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/svm/event_confirmer.go:getRequiredConfirmations
- Entrypoint: create user-controlled SVM activity whose signatures fall exactly on batch boundaries
- Attacker controls: start slot, last processed slot, and chunk boundaries used by the slot scanner
- Exploit idea: skip a real user event during slot progression so funds stay permanently unobserved or unrefunded
- Invariant to test: restart and resume logic never reclassifies or duplicates already-seen SVM traffic
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: place valid events exactly at slot and batch edges, restart the listener, and verify no event is skipped or duplicated
