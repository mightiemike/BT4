# Q2683: query-execution encoding mismatch in Sha256Hash.toString

## Question
Can an unprivileged attacker abuse /wallet/broadcasttransaction so common/src/main/java/org/tron/common/utils/Sha256Hash.java::toString returns one canonical object on the query/build path but a different byte form is later consumed by execution or broadcast, letting the mismatch reach Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: common/src/main/java/org/tron/common/utils/Sha256Hash.java::toString
- Entrypoint: /wallet/broadcasttransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Compare address and byte formatting helpers used in displayed results, built transactions, and final execution paths.
- Invariant to test: The object shown to the user and the bytes later executed against must be derived from one identical canonical representation.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Chain the relevant read/build path and the later execution path around /wallet/broadcasttransaction; assert they agree on the same canonical bytes and selected object.
