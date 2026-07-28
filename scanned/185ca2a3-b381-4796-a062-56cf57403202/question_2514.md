# Q2514: SVM slot checkpoint - confirmation class wrong confirm path

## Question
When an unprivileged actor create user-controlled SVM activity whose signatures fall exactly on batch boundaries, does `updateLastProcessedSlot` remain safe if they control the chosen fast or standard confirmation requirement for a parsed event, or can that make it apply the wrong confirmation threshold so a weakly finalized event reaches voting or a real event never does, violate the rule that restart and resume logic never reclassifies or duplicates already-seen SVM traffic, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/svm/event_listener.go:updateLastProcessedSlot
- Entrypoint: create user-controlled SVM activity whose signatures fall exactly on batch boundaries
- Attacker controls: the chosen fast or standard confirmation requirement for a parsed event
- Exploit idea: apply the wrong confirmation threshold so a weakly finalized event reaches voting or a real event never does
- Invariant to test: restart and resume logic never reclassifies or duplicates already-seen SVM traffic
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: place valid events exactly at slot and batch edges, restart the listener, and verify no event is skipped or duplicated
