# Q4764: zk_elgamal_proof::process_verify_proof - context state written for a failed verification

## Question
Can an unprivileged attacker who submits zk-ElGamal proof verification instructions with proofs and context it authored, invoking the proof instruction through CPI from its own program, drive `zk_elgamal_proof::process_verify_proof` to have proof context recorded even though verification did not succeed, so that the invariant that context state is written only after successful verification is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `programs/zk-elgamal-proof/src/lib.rs` -> `process_verify_proof`
- Entrypoint: submits zk-ElGamal proof verification instructions with proofs and context it authored, invoking the proof instruction through CPI from its own program
- Attacker controls: the proof bytes, the proof context data, the context state account and its authority
- Exploit idea: Have proof context recorded even though verification did not succeed.
- Invariant to test: Context state is written only after successful verification.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test process_verify_proof with the crafted proof and assert verification fails and no context is written
