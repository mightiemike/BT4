No vulnerability found for this question.

**Analysis:**

Both `verify` and `verify_slow` ultimately reduce the check to the same abelian-group identity, and duplicated point indices across (or within) `Representation` rows cannot cause divergence, because the transformation is a pure application of distributivity/commutativity of Ristretto255 group addition — not an approximation.

`verify_slow` computes each of the `m` entries independently via `multi_scalar_mul(repr.to_points(stmt), repr.get_scalars())` for both `psi_sigma` and `fx`, then compares the two vectors entrywise after adding `_A` and scaling by `e`: [1](#0-0) [2](#0-1) 

`verify` instead batches the `m` per-entry equations `A[i] + e f(X)[i] - psi(sigma)[i] = 0` into one randomized linear combination `sum_i beta_i * (...)`, and flattens all the underlying `(base, scalar)` pairs from every `Representation` (across `A`, scaled `f(X)`, and scaled `psi(sigma)`) into a single `bases`/`scalars` array before calling one `multi_scalar_mul`: [3](#0-2) 

Because `to_points` is a pure lookup (`stmt.get_point(idx).point_clone()`) with no side effects, and because Ristretto255 point addition/scalar multiplication is linear, the sum $\sum_i \beta_i (\sum_j a_{ij} P_{ij})$ is *identical* to $\sum_{i,j} \beta_i a_{ij} P_{ij}$ irrespective of whether some $P_{ij}$ (i.e., some `point_idxs` entries) are repeated across different `Representation` rows or within the same row: [4](#0-3) 

Regrouping the summands into one flat MSM call versus computing `m` separate MSMs and then combining the resulting points is exactly the associative/commutative regrouping of an abelian-group sum — it changes nothing about the value being computed. The code even documents this reuse explicitly as a performance (not correctness) consideration: [5](#0-4) 

The `RepresentationVec::scale_each`/`scale_all` operations scale by row-index `i` (positionally, matching the equation index in `[m]`), not by point index, so duplicate point indices have no interaction with the `beta`/`e` scaling logic either: [6](#0-5) 

Therefore, a `RepresentationVec` with a repeated point index at two different `Representation` rows does not create any divergence between the fast MSM-batched `verify` and the slow per-vector `verify_slow` — both compute the exact same underlying group-element sum, just via different (but algebraically equivalent) groupings/orderings of the same multiplication-and-addition terms. There is no soundness gap opened in the fast path by index duplication.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_homomorphism.move (L67-80)
```text
    #[test_only]
    /// Computes and returns $\psi(X, w) \in \mathbb{G}^m$ given the public statement $X$ and the secret witness $w$.
    public(friend) inline fun evaluate_psi<P>(psi: Homomorphism<P>,
                                   stmt: &Statement<P>,
                                   witn: &Witness): vector<RistrettoPoint> {
        psi(stmt, witn).map_ref(|repr| multi_scalar_mul(&repr.to_points(stmt), repr.get_scalars()))
    }

    #[test_only]
    /// Returns $f(X) \in \mathbb{G}^m$ given the public statement $X$.
    public(friend) inline fun evaluate_f<P>(f: TransformationFunction<P>,
                                 stmt: &Statement<P>): vector<RistrettoPoint> {
        f(stmt).map_ref(|repr| multi_scalar_mul(&repr.to_points(stmt), repr.get_scalars()))
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol.move (L101-119)
```text
        // Step 3: Compute the `m` entries of `f(X)`
        let fx = evaluate_f(|_X| f(_X), stmt);
        assert!(fx.length() == _A.length(), error::invalid_argument(E_PROOF_COMMITMENT_WRONG_LEN));

        // Step 4: Compute the `m` entries of \psi(X, w)
        let psi_sigma = evaluate_psi(|_X, w| psi(_X, w), stmt, &sigma);
        assert!(psi_sigma.length() == _A.length(), error::invalid_argument(E_PROOF_COMMITMENT_WRONG_LEN));

        equal_vec_points(
            &psi_sigma,
            &add_vec_points(
                _A,
                &mul_points(
                    &fx,
                    &e
                ),
            )
        )
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol.move (L177-180)
```text
        // "Scale" the `i`th reprentation in `\psi` by `-\beta[i]`
        // TODO(Perf): I think this could be sub-optimal: we will redo the same \beta[i] \sigma[j] multiplication several times
        //   when a `RepresentationVec`'s row reuses \sigma[j].
        psi_sigma.scale_each(&neg_scalars(&betas));
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol.move (L182-209)
```text
        // We start with an empty MSM: \sum_{i \in m} 0
        // ...and extend it to: \sum_{i \in [m]} A[i]^{\beta[i]}
        //                                          ^^^^^^^^^^^^^^^
        let bases = points_clone(_A);
        let scalars = betas;

        // These asserts will only fail when we have mis-implemented the cloning of `A` above
        assert!(bases.length() == m, error::internal(E_INTERNAL_INVARIANT_FAILED));
        assert!(scalars.length() == m, error::internal(E_INTERNAL_INVARIANT_FAILED));

        // Extend MSM to: be \sum_{i \in [m]} A[i]^\beta[i] + \beta[i] ( e f(stmt)[i] )
        //                                                    ^^^^^^^^^^^^^^^^^^^^^^^^^
        efx.for_each_ref(|repr| {
            bases.append(repr.to_points(stmt));
            scalars.append(*repr.get_scalars());
        });

        // Extend MSM to: be \sum_{i \in [m]} A[i]^\beta[i] + \beta[i] ( e f(stmt)[i] ) - \beta[i] (\psi(\sigma)[i])
        //                                                                                ^^^^^^^^^^^^^^^^^^^^^^^^^^
        psi_sigma.for_each_ref(|repr| {
            bases.append(repr.to_points(stmt));
            scalars.append(*repr.get_scalars());
        });

        // TODO(Perf): Could combine exponents for shared bases more aggresively? Or does the MSM code do it implicitly?

        // Do the MSM and check it equals the (zero) identity
        multi_scalar_mul(&bases, &scalars).point_equals(&point_identity())
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_representation.move (L53-55)
```text
    public(friend) fun to_points<P>(self: &Representation, stmt: &Statement<P>): vector<RistrettoPoint> {
        self.point_idxs.map(|idx| stmt.get_point(idx).point_clone())
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_representation_vec.move (L50-59)
```text
    public(friend) fun scale_all(self: &mut RepresentationVec, e: &Scalar) {
        self.v.for_each_mut(|repr| repr.scale(e));
    }

    /// For all $i$, multiply the $i$th representation by `b[i]` (i.e., multiply `self.v[i].scalars` by `b[i]`)
    public(friend) fun scale_each(self: &mut RepresentationVec, b: &vector<Scalar>) {
        self.v.enumerate_mut(|i, repr| {
            repr.scale(&b[i])
        });
    }
```
