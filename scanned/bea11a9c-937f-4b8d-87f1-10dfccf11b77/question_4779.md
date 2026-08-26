# Q4779: zk_elgamal_proof::process_verify_proof - context data reused across different proofs (reusing a context account created by)

## Question
Can an unprivileged attacker who submits zk-ElGamal proof verification instructions with proofs and context it authored, reusing a context account created by a different transaction, drive `zk_elgamal_proof::process_verify_proof` to have a context produced for one proof accepted as the context of another, so that the invariant that each verified context is bound to the exact proof that produced it is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/zk-elgamal-proof/src/lib.rs` -> `process_verify_proof`
- Entrypoint: submits zk-ElGamal proof verification instructions with proofs and context it authored, reusing a context account created by a different transaction
- Attacker controls: the proof bytes, the proof context data, the context state account and its authority
- Exploit idea: Have a context produced for one proof accepted as the context of another.
- Invariant to test: Each verified context is bound to the exact proof that produced it.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test process_verify_proof with the crafted proof and assert verification fails and no context is written
