# Q2969: key-derivation confusion in BN128.add

## Question
Can an unprivileged attacker abuse /jsonrpc eth_sendRawTransaction so crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java::add derives or accepts an alternate key/address/view that resolves to a different owner than the caller expects, and then chain that confusion into Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: crypto/src/main/java/org/tron/common/crypto/zksnark/BN128.java::add
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Test alternate key encodings, truncated inputs, mixed viewing/spending key material, and address derivation edge cases.
- Invariant to test: Every accepted key, viewing key, or address form must resolve to one owner and one spend or view context.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Generate edge-case key/address material through /jsonrpc eth_sendRawTransaction; assert no decoded or derived form aliases another live owner or spend authority.
