# Q0594: EVM signing hash build - value encoding nonce collision

## Question
When an unprivileged actor create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields, does `GetOutboundSigningRequest` remain safe if they control recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call, or can that make it make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, violate the rule that each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:GetOutboundSigningRequest
- Entrypoint: create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields
- Attacker controls: recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: fuzz asset address emptiness, tx type, revert message, and payload shape, then check whether the chosen vault function ever diverges from the intended mode
