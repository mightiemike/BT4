# Q2516: SVM confirm selection - confirmation class wrong confirm path

## Question
If a user create user-controlled SVM activity whose signatures fall exactly on batch boundaries, can `getRequiredConfirmations` be pushed into a path where the chosen fast or standard confirmation requirement for a parsed event causes it to apply the wrong confirmation threshold so a weakly finalized event reaches voting or a real event never does, so that restart and resume logic never reclassifies or duplicates already-seen SVM traffic no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_confirmer.go:getRequiredConfirmations
- Entrypoint: create user-controlled SVM activity whose signatures fall exactly on batch boundaries
- Attacker controls: the chosen fast or standard confirmation requirement for a parsed event
- Exploit idea: apply the wrong confirmation threshold so a weakly finalized event reaches voting or a real event never does
- Invariant to test: restart and resume logic never reclassifies or duplicates already-seen SVM traffic
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: place valid events exactly at slot and batch edges, restart the listener, and verify no event is skipped or duplicated
