# Q3923: SVM resume slot - confirmation class double observe

## Question
If a user restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state, can `getStartSlot` be pushed into a path where the chosen fast or standard confirmation requirement for a parsed event causes it to observe the same user event twice across restart or chunk boundaries and send conflicting downstream work, so that restart and resume logic never reclassifies or duplicates already-seen SVM traffic no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_listener.go:getStartSlot
- Entrypoint: restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state
- Attacker controls: the chosen fast or standard confirmation requirement for a parsed event
- Exploit idea: observe the same user event twice across restart or chunk boundaries and send conflicting downstream work
- Invariant to test: restart and resume logic never reclassifies or duplicates already-seen SVM traffic
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: place valid events exactly at slot and batch edges, restart the listener, and verify no event is skipped or duplicated
