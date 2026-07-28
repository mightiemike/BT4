# Q1515: EVM height checkpoint - topic binding partial decode

## Question
If a user emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, can `updateLastProcessedBlock` be pushed into a path where indexed topics for sender, recipient, tx hash, and log index causes it to accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, so that the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/event_listener.go:updateLastProcessedBlock
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: mutate offsets, topic counts, and trailing words, then diff parsed `EventData` against the original ABI payload before and after `constructInbound`
