# Q2450: EVM signature-data decode - value fields early confirm

## Question
When an unprivileged actor emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, does `decodeSignatureData` remain safe if they control token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload, or can that make it misclassify the event kind or confirmation class so a low-finality or wrong-method event reaches voting too early, violate the rule that the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/event_parser.go:decodeSignatureData
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: token, amount, revert recipient, tx type, and `fromCEA` bits encoded in the event payload
- Exploit idea: misclassify the event kind or confirmation class so a low-finality or wrong-method event reaches voting too early
- Invariant to test: the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: mutate offsets, topic counts, and trailing words, then diff parsed `EventData` against the original ABI payload before and after `constructInbound`
