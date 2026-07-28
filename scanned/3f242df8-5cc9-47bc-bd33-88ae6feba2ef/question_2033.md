# Q2033: SVM parser dispatch - address encoding address confusion

## Question
When an unprivileged actor emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index, does `ParseEvent` remain safe if they control base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields, or can that make it normalize user-controlled addresses into a different economic target than the source chain intended, violate the rule that only well-formed SVM gateway bytes can become an inbound or outbound observation, and end in unauthorized mint, release, or refund of value on Push Chain or the destination chain?

## Target
- File/function: universalClient/chains/svm/event_parser.go:ParseEvent
- Entrypoint: emit several user-controlled gateway logs in one Solana transaction and let the listener parse them by signature and log index
- Attacker controls: base58 or raw byte encodings for sender, recipient, token mint, and revert recipient fields
- Exploit idea: normalize user-controlled addresses into a different economic target than the source chain intended
- Invariant to test: only well-formed SVM gateway bytes can become an inbound or outbound observation
- Expected Immunefi impact: unauthorized mint, release, or refund of value on Push Chain or the destination chain
- Fast validation: toggle base58, zero bytes, and alternate-length address encodings and inspect whether economic meaning changes after normalization
