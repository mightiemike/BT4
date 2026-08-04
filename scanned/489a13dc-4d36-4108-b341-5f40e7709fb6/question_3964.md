# Q3964: transparent-shielded accounting drift in KeyIo.decodePaymentAddress

## Question
Can an unprivileged attacker use /jsonrpc eth_sendRawTransaction so framework/src/main/java/org/tron/core/zen/address/KeyIo.java::decodePaymentAddress moves value between transparent and shielded state with inconsistent fee or amount handling, making the canonical byte representation or derived key/address and the intended owner, transaction context, or verification result diverge and leading to Signature, address, or proof confusion that lets the wrong actor authorize or spend?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/address/KeyIo.java::decodePaymentAddress
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: byte arrays, hex/base58/bech32 strings, signatures, proofs, hashes, derivation inputs, and address encoding flags
- Exploit idea: Focus on transparent-from, transparent-to, fee, and note-value interactions, especially when some branches are optional.
- Invariant to test: Transfers between transparent and shielded representations must conserve value exactly except for the intended fee burn.
- Expected Immunefi impact: Signature, address, or proof confusion that lets the wrong actor authorize or spend
- Fast validation: Fuzz every combination of transparent/shielded inputs through /jsonrpc eth_sendRawTransaction; assert net value conservation across both representations plus fees.
