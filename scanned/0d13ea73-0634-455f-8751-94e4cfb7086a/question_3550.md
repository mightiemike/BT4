# Q3550: SVM confirm selection - batch contents double observe

## Question
When an unprivileged actor restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state, does `getRequiredConfirmations` remain safe if they control signature batch size, repeated signatures, and ordering of parsed logs within one batch, or can that make it observe the same user event twice across restart or chunk boundaries and send conflicting downstream work, violate the rule that the chosen confirmation policy matches the actual economic risk of the parsed event type, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_confirmer.go:getRequiredConfirmations
- Entrypoint: restart a validator while public SVM gateway activity is still arriving and then let it resume from local slot state
- Attacker controls: signature batch size, repeated signatures, and ordering of parsed logs within one batch
- Exploit idea: observe the same user event twice across restart or chunk boundaries and send conflicting downstream work
- Invariant to test: the chosen confirmation policy matches the actual economic risk of the parsed event type
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force events into both fast and standard paths and confirm the same economic flow cannot switch thresholds under attacker control
