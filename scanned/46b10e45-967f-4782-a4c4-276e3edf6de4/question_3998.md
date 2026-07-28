# Q3998: Broadcaster signed scan - receipt outcome stuck broadcast

## Question
If a user trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing, can `processSigned` be pushed into a path where whether the destination receipt is not found, insufficiently confirmed, reverted, or successful causes it to leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure, so that normal user outbounds eventually reach a correct terminal state instead of looping forever no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/txbroadcaster/broadcaster.go:processSigned
- Entrypoint: trigger a public flow that reaches the broadcaster or resolver with an empty or delayed tx hash after signing
- Attacker controls: whether the destination receipt is not found, insufficiently confirmed, reverted, or successful
- Exploit idea: leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure
- Invariant to test: normal user outbounds eventually reach a correct terminal state instead of looping forever
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: force a dropped or replaced transaction on a local EVM chain and see whether the same outbound is incorrectly refunded, replayed, or duplicated
