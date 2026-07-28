# Q3061: EVM nonce mark - broadcast state wrong rewind

## Question
If a user trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing, can `checkNonceAndMarkBroadcasted` be pushed into a path where `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound causes it to rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution, so that normal user outbounds eventually reach a correct terminal state instead of looping forever no longer holds and the result is permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txbroadcaster/evm.go:checkNonceAndMarkBroadcasted
- Entrypoint: trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing
- Attacker controls: `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound
- Exploit idea: rewind a live outbound to `SIGNED` when it should have been terminal, enabling replay or duplicate execution
- Invariant to test: normal user outbounds eventually reach a correct terminal state instead of looping forever
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force a dropped or replaced transaction on a local EVM chain and see whether the same outbound is incorrectly refunded, replayed, or duplicated
