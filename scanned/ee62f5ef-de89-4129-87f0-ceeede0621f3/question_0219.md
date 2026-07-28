# Q0219: EVM function select - function choice nonce collision

## Question
Can an unprivileged attacker create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields and use control over `TxType`, asset address emptiness, and payload shape used to choose the vault function name so that `determineFunctionName` make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, breaking the invariant that an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state and leading to direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:determineFunctionName
- Entrypoint: create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields
- Attacker controls: `TxType`, asset address emptiness, and payload shape used to choose the vault function name
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: queue several outbounds to the same chain, drop or replace one EVM tx, and see whether another outbound incorrectly inherits its nonce state
