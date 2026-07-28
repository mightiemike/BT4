# Q3456: SVM confirm selection - batch contents skip window

## Question
If a user restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state, can `getRequiredConfirmations` be pushed into a path where signature batch size, repeated signatures, and ordering of parsed logs within one batch causes it to skip a real user event during slot progression so funds stay permanently unobserved or unrefunded, so that restart and resume logic never reclassifies or duplicates already-seen SVM traffic no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_confirmer.go:getRequiredConfirmations
- Entrypoint: restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state
- Attacker controls: signature batch size, repeated signatures, and ordering of parsed logs within one batch
- Exploit idea: skip a real user event during slot progression so funds stay permanently unobserved or unrefunded
- Invariant to test: restart and resume logic never reclassifies or duplicates already-seen SVM traffic
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: place valid events exactly at slot and batch edges, restart the listener, and verify no event is skipped or duplicated
