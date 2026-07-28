# Q3452: SVM signature batch - batch contents skip window

## Question
When an unprivileged actor restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state, does `processSignatureBatch` remain safe if they control signature batch size, repeated signatures, and ordering of parsed logs within one batch, or can that make it skip a real user event during slot progression so funds stay permanently unobserved or unrefunded, violate the rule that restart and resume logic never reclassifies or duplicates already-seen SVM traffic, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_listener.go:processSignatureBatch
- Entrypoint: restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state
- Attacker controls: signature batch size, repeated signatures, and ordering of parsed logs within one batch
- Exploit idea: skip a real user event during slot progression so funds stay permanently unobserved or unrefunded
- Invariant to test: restart and resume logic never reclassifies or duplicates already-seen SVM traffic
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: place valid events exactly at slot and batch edges, restart the listener, and verify no event is skipped or duplicated
