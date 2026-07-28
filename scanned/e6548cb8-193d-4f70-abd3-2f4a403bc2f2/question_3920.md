# Q3920: SVM slot polling - confirmation class double observe

## Question
When an unprivileged actor restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state, does `processNewSlots` remain safe if they control the chosen fast or standard confirmation requirement for a parsed event, or can that make it observe the same user event twice across restart or chunk boundaries and send conflicting downstream work, violate the rule that restart and resume logic never reclassifies or duplicates already-seen SVM traffic, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_listener.go:processNewSlots
- Entrypoint: restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state
- Attacker controls: the chosen fast or standard confirmation requirement for a parsed event
- Exploit idea: observe the same user event twice across restart or chunk boundaries and send conflicting downstream work
- Invariant to test: restart and resume logic never reclassifies or duplicates already-seen SVM traffic
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: place valid events exactly at slot and batch edges, restart the listener, and verify no event is skipped or duplicated
