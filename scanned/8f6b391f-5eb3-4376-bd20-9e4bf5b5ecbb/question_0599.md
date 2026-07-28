# Q0599: EVM already-executed check - value encoding nonce collision

## Question
Can an unprivileged attacker create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields and use control over recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call so that `IsAlreadyExecuted` make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, breaking the invariant that each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch and leading to unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields
- Attacker controls: recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: fuzz asset address emptiness, tx type, revert message, and payload shape, then check whether the chosen vault function ever diverges from the intended mode
