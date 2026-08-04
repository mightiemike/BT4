# Q2891: owner-derivation confusion in ECKeyAgreement.getInstance

## Question
Can an unprivileged attacker choose public inputs through /wallet/scanshieldedtrc20notesbyivk so crypto/src/main/java/org/tron/common/crypto/jce/ECKeyAgreement.java::getInstance derives or recovers a different owner than the caller-facing data implies, and then chain that confusion into Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/jce/ECKeyAgreement.java::getInstance
- Entrypoint: /wallet/scanshieldedtrc20notesbyivk
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Probe recovery ids, prefix bytes, chain-specific address prefixes, and alternate key forms that may resolve to different owners.
- Invariant to test: Owner derivation and recovery must be stable, canonical, and identical across every caller-visible and execution path.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Generate edge-case owner-recovery inputs through /wallet/scanshieldedtrc20notesbyivk; assert every accepted form maps to one and only one live owner.
