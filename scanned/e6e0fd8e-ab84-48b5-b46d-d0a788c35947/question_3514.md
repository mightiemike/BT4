# Q3514: EVM gas-used read - value encoding mode confusion

## Question
If a user cause many public Push-chain outbounds to the same EVM chain to queue concurrently, can `GetGasFeeUsed` be pushed into a path where recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call causes it to switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain, so that an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state no longer holds and the result is unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:GetGasFeeUsed
- Entrypoint: cause many public Push-chain outbounds to the same EVM chain to queue concurrently
- Attacker controls: recipient, asset address, amount, gas price, gas limit, and revert message fields encoded into the destination call
- Exploit idea: switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain
- Invariant to test: an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: force success, revert, not-found, and mempool-drop cases and verify the resolver never marks the wrong terminal outcome
