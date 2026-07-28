# Q1750: Signed nonce read - broadcast state stuck broadcast

## Question
When an unprivileged actor cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction, does `ReadSignedNonce` remain safe if they control `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound, or can that make it leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure, violate the rule that nonce-based resolution decisions are tied to the intended outbound rather than unrelated same-signer traffic, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/tss/txflow/parse.go:ReadSignedNonce
- Entrypoint: cause a public outbound to race with other same-signer traffic so the signed nonce becomes consumed by a different chain transaction
- Attacker controls: `SIGNED`, `BROADCASTED`, and rewound states plus any stored tx hash for the outbound
- Exploit idea: leave a user outbound forever in `BROADCASTED` or `SIGNED` because retry logic cannot distinguish safe retry from terminal failure
- Invariant to test: nonce-based resolution decisions are tied to the intended outbound rather than unrelated same-signer traffic
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: trace one outbound through repeated `SIGNED`/`BROADCASTED` transitions and confirm it cannot loop forever under user-controlled inputs
