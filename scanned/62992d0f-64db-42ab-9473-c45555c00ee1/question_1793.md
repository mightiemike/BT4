# Q1793: EVM block polling - topic binding double record

## Question
When an unprivileged actor emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, does `processNewBlocks` remain safe if they control indexed topics for sender, recipient, tx hash, and log index, or can that make it create duplicate or conflicting local records that later produce double voting, double execution, or a permanent stuck retry loop, violate the rule that every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/event_listener.go:processNewBlocks
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: create duplicate or conflicting local records that later produce double voting, double execution, or a permanent stuck retry loop
- Invariant to test: every observed EVM log maps to exactly one canonical `EventData` payload and one confirmation policy
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force a shallow reorg or replay around the confirmer window and check whether conflicting `EventData` rows or duplicate vote attempts appear
