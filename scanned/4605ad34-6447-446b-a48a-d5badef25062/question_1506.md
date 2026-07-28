# Q1506: EVM sendFunds ingest - topic binding partial decode

## Question
Can an unprivileged attacker emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries and use control over indexed topics for sender, recipient, tx hash, and log index so that `parseSendFundsEvent` accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation, breaking the invariant that the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/event_parser.go:parseSendFundsEvent
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: accept a partially decoded EVM event as if it were a complete canonical inbound or outbound observation
- Invariant to test: the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: mutate offsets, topic counts, and trailing words, then diff parsed `EventData` against the original ABI payload before and after `constructInbound`
