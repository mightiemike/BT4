# Q1280: Signed nonce read - signed payload false revert

## Question
If a user create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access, can `ReadSignedNonce` be pushed into a path where the persisted signed hash and signature bytes carried through rebroadcast and resolution causes it to vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere, so that normal user outbounds eventually reach a correct terminal state instead of looping forever no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSignedNonce
- Entrypoint: create a public outbound to EVM and induce a normal mempool-drop or not-found condition reachable without privileged access
- Attacker controls: the persisted signed hash and signature bytes carried through rebroadcast and resolution
- Exploit idea: vote failure and trigger refund logic even though the original outbound can still land or already landed elsewhere
- Invariant to test: normal user outbounds eventually reach a correct terminal state instead of looping forever
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: trace one outbound through repeated `SIGNED`/`BROADCASTED` transitions and confirm it cannot loop forever under user-controlled inputs
