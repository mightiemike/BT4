No vulnerability found for this question.

**Analysis:**

The scenario in the question assumes betas are "derived after σ is fixed," which matches the actual implementation — but that is exactly the property that prevents the described attack, not a weakness enabling it.

In `sigma_protocol::verify`, the challenge `e` and the batching coefficients `betas` are both derived via `fiat_shamir`, which is called with `proof.get_response()` (i.e., σ) as an input to the *second* stage of the hash [1](#0-0) . Looking at `fiat_shamir` itself, `e` is derived from a transcript that excludes σ (fixed before the prover computes σ = α + e·w), while `betas` are derived from a second hash that appends the `sigmas` bytes to the seed [2](#0-1) .

This means `betas = H(dst, stmt, A, σ)` is a cryptographic hash output that the attacker cannot algebraically invert. For the attacker's proposed attack to work — engineering a σ such that a forged relation's error term `e_forged` cancels a legitimate relation's slack `e_legit` under a specific `β^i` — they would need to find a σ satisfying the *algebraic* constraint `Σ_i β_i(σ)·e_i(σ) = 0` (with not all `e_i(σ)=0`), where `β_i(σ)` is itself a SHA2-512-derived function of that same σ. This is a circular fixed-point search against a preimage-resistant hash, which is computationally infeasible under standard cryptographic assumptions (equivalent in spirit to a random-oracle argument, akin to a Schwartz–Zippel-style batching soundness bound).

This exact reasoning is documented and pinned by a regression test in the codebase: `beta_changes_with_sigma_e_does_not`, which explicitly states "Soundness of the aggregated check ... requires β to be unpredictable to the prover at the moment σ is committed. That requires σ to be part of the Fiat-Shamir transcript that derives β" and asserts that changing σ changes β while e (fixed before σ) does not change [3](#0-2) .

Additionally, `verify_slow` (the non-batched, per-relation check) is asserted to agree with the batched `verify` in `assert_correctly_computed_proof_verifies` for every relation type (PedEq, Schnorr, withdraw, transfer, key rotation) [4](#0-3) , confirming that no crafted σ can satisfy the aggregated MSM check `Σ β_i(A_i + e·f_i(X) − ψ_i(σ)) = 0` while any individual `ψ_i(σ) ≠ A_i + e·f_i(X)`, short of a hash preimage break.

Because the described attack path depends on breaking SHA2-512 preimage resistance under an algebraic constraint rather than on any flaw in the Move implementation, storage/proof binding, or transaction-processing logic, this does not meet the review's decision standard for a state-integrity-impacting vulnerability.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol.move (L157-162)
```text
        // Step 1: Fiat-Shamir transform on `(dst, (psi, f), stmt)` to derive the random challenge `e`
        let _A = proof.get_commitment();
        let m = _A.length();
        let (e, betas) = fiat_shamir(dst, stmt, proof.get_compressed_commitment(),
            proof.get_response(), proof.get_response_length());

```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol.move (L226-261)
```text
    public(friend) inline fun assert_correctly_computed_proof_verifies<P>(
        dst: DomainSeparator,
        stmt: Statement<P>,
        witn: Witness,
        psi: Homomorphism<P>,
        f: TransformationFunction<P>,
    ): (Proof, Witness) {
        let (proof, alpha) = prove(
            dst,
            |_X, w| psi(_X, w),
            &stmt,
            &witn
        );

        // Make sure the sigma protocol proof verifies (slowly)
        assert!(
            verify_slow(
                dst,
                |_X, w| psi(_X, w),
                |_X| f(_X),
                &stmt,
                &proof
            ), error::invalid_argument(E_SLOW_VERIFICATION_FAILED));

        // Make sure the sigma protocol proof verifies (quickly)
        assert!(
            verify(
                dst,
                |_X, w| psi(_X, w),
                |_X| f(_X),
                &stmt,
                &proof
            ), error::invalid_argument(E_FAST_VERIFICATION_FAILED));

        (proof, alpha)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_fiat_shamir.move (L112-131)
```text
        let e = new_scalar_uniform_from_64_bytes(sha2_512(seed)).extract();

        *seed.last_mut() += 1;
        assert!(*seed.last() == 1, error::internal(E_INTERNAL_INVARIANT_FAILED));
        seed.append(bcs::to_bytes(sigmas));

        // i.e., SHA2-512(
        //         SHA2-512(BCS{ dst, type_name, k, stmt_X, stmt_x, proof_A })
        //         || 0x01
        //         || BCS{ sigmas }
        //       )
        let beta = new_scalar_uniform_from_64_bytes(sha2_512(seed)).extract();

        let betas = vector[];
        let prev_beta = scalar_one();
        betas.push_back(prev_beta);
        for (_i in 1..m) {
            prev_beta = prev_beta.scalar_mul(&beta);
            betas.push_back(prev_beta);
        };
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_fiat_shamir.move (L151-186)
```text
    #[test]
    /// Regression test pinning every binding of the Fiat-Shamir transcript.
    ///
    /// Soundness of the aggregated check
    ///
    /// $$\sum_i \beta^i \cdot (\psi_i(\sigma) - A_i - e \cdot f_i(X)) = 0$$
    ///
    /// requires $\beta$ to be unpredictable to the prover at the moment $\sigma$ is committed. That requires $\sigma$
    /// to be part of the Fiat-Shamir transcript that derives $\beta$. Conversely, $e$ MUST NOT depend on $\sigma$ —
    /// the honest prover computes $\sigma = \alpha + e \cdot w$, so $e$ must be fixed before $\sigma$ exists.
    /// $e$ MUST, however, depend on the rest of the public transcript (the statement and the prover's commitment
    /// $A$); otherwise the verifier would be replaying a fixed challenge across distinct statements.
    ///
    /// Holding the rest of the transcript fixed and varying one input at a time, this test pins:
    ///   - changing $\sigma$ MUST change $\beta$ and MUST NOT change $e$ (the bounty regression);
    ///   - changing $A$ MUST change both $e$ and $\beta$;
    ///   - changing the statement MUST change both $e$ and $\beta$.
    fun beta_changes_with_sigma_e_does_not() {
        let dst = new_domain_separator(@aptos_framework, 4u8, b"fs regression", b"session");
        let stmt = new_statement<TestProtocol>(vector[basepoint(), basepoint_H()], vector[basepoint_compressed(), basepoint_H_compressed()], vector[]);

        // $m = 2$ $\Rightarrow$ `betas` = $[1, \beta]$; `betas[1]` is the raw $\beta$ value.
        let _A = vector[point_identity_compressed(), point_identity_compressed()];
        let k = 1;

        let sigmas_a = vector[new_scalar_from_u64(7)];
        let sigmas_b = vector[new_scalar_from_u64(8)];

        let (e_A, betas_A) = fiat_shamir<TestProtocol>(dst, &stmt, &_A, &sigmas_a, k);
        let (e_b, betas_b) = fiat_shamir<TestProtocol>(dst, &stmt, &_A, &sigmas_b, k);

        // (1) $\sigma$-binding.
        // $e$: derived from a transcript that excludes $\sigma$ — must be invariant.
        assert!(e_A.scalar_equals(&e_b), 1);
        // $\beta$: derived from a transcript that includes $\sigma$ — must change with $\sigma$.
        assert!(!betas_A[1].scalar_equals(&betas_b[1]), 2);
```
