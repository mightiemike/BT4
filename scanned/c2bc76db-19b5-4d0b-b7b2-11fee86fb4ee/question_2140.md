# Q2140: SVM confirm selection - batch contents wrong confirm path

## Question
When an unprivileged actor create user-controlled SVM activity whose signatures fall exactly on batch boundaries, does `getRequiredConfirmations` remain safe if they control signature batch size, repeated signatures, and ordering of parsed logs within one batch, or can that make it apply the wrong confirmation threshold so a weakly finalized event reaches voting or a real event never does, violate the rule that the chosen confirmation policy matches the actual economic risk of the parsed event type, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_confirmer.go:getRequiredConfirmations
- Entrypoint: create user-controlled SVM activity whose signatures fall exactly on batch boundaries
- Attacker controls: signature batch size, repeated signatures, and ordering of parsed logs within one batch
- Exploit idea: apply the wrong confirmation threshold so a weakly finalized event reaches voting or a real event never does
- Invariant to test: the chosen confirmation policy matches the actual economic risk of the parsed event type
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force events into both fast and standard paths and confirm the same economic flow cannot switch thresholds under attacker control
