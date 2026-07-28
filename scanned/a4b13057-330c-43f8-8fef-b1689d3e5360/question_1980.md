# Q1980: EVM signature-data decode - abi offsets field confusion

## Question
When an unprivileged actor emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries, does `decodeSignatureData` remain safe if they control dynamic ABI offsets for payload bytes and signature data inside log data, or can that make it bind the right `EventID` to the wrong decoded fields so one user transaction is interpreted as a different transfer or execution, violate the rule that the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/evm/event_parser.go:decodeSignatureData
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: dynamic ABI offsets for payload bytes and signature data inside log data
- Exploit idea: bind the right `EventID` to the wrong decoded fields so one user transaction is interpreted as a different transfer or execution
- Invariant to test: the `txHash:logIndex` identity never points to conflicting `EventData` or vote contents
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: mutate offsets, topic counts, and trailing words, then diff parsed `EventData` against the original ABI payload before and after `constructInbound`
