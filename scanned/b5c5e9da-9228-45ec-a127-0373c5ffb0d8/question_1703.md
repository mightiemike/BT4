# Q1703: EVM height checkpoint - topic binding early confirm

## Question
Can an unprivileged attacker emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries and use control over indexed topics for sender, recipient, tx hash, and log index so that `updateLastProcessedBlock` misclassify the event kind or confirmation class so a low-finality or wrong-method event reaches voting too early, breaking the invariant that reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted` and leading to permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/event_listener.go:updateLastProcessedBlock
- Entrypoint: emit multiple user-controlled gateway logs in one public EVM transaction and let the listener process them across chunk boundaries
- Attacker controls: indexed topics for sender, recipient, tx hash, and log index
- Exploit idea: misclassify the event kind or confirmation class so a low-finality or wrong-method event reaches voting too early
- Invariant to test: reorged, malformed, or wrong-method EVM logs never reach `StatusCompleted`
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: emit crafted gateway logs on a fork or local EVM devnet and compare raw log bytes against the persisted `store.Event` row and the resulting vote message
