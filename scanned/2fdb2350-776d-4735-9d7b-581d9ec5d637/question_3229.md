# Q3229: Wallet: createTransaction owner spoof

## Question
Can an unprivileged attacker (gRPC/HTTP Wallet API) abuse `Wallet.createTransactionCapsuleWithoutValidateWithTimeout` in `framework/src/main/java/org/tron/core/Wallet.java` — where the attacker calls Wallet.createTransactionCapsuleWithoutValidateWithTimeout with an owner_address they do not control expecting the node to sign or accept it without a valid signature — to break the invariant that transactions mutate only accounts whose signature is present and sufficient, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `framework/src/main/java/org/tron/core/Wallet.java` -> `Wallet.createTransactionCapsuleWithoutValidateWithTimeout`
- Entrypoint: gRPC/HTTP Wallet.createTransactionCapsuleWithoutValidateWithTimeout with foreign owner_address
- Attacker controls: request/transaction/contract inputs to `Wallet.createTransactionCapsuleWithoutValidateWithTimeout` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls Wallet.createTransactionCapsuleWithoutValidateWithTimeout with an owner_address they do not control expecting the node to sign or accept it without a valid signature
- Invariant to test: transactions mutate only accounts whose signature is present and sufficient
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: build a tx for a foreign owner and assert broadcast rejects unsigned
