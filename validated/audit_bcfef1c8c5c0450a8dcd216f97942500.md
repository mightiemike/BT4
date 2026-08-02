No vulnerability found for this question.

**Reasoning:**

The premise misunderstands both the data flow and the math of multi-scalar multiplication (MSM):

1. **`point_idxs` are not attacker-controlled input.** `new_representation` is restricted to the module's own `friend` list [1](#0-0) , and every call site builds `point_idxs` from hardcoded constants and loop counters inside `psi`/`f`, e.g. `sigma_protocol_transfer.move`'s balance-equation construction [2](#0-1) . An unprivileged caller supplies only the witness scalars via a proof response; there is no code path where a transaction/proof input can inject arbitrary or duplicated `point_idxs` into a `Representation`.

2. **Duplicate `point_idxs` are not a bug — they're the intended encoding of a sum.** The MSM formula $\sum a_i G_i$ is well-defined even when some $G_i$ repeat; repeating an index with different scalars is exactly how "coefficient accumulation on the same base" (e.g., `dk · B^i · old_R[i]` plus `new_a[i] · B^i · G` both hitting `IDX_G`) is expressed. This is demonstrably used on purpose in `psi` for withdrawals/transfers [3](#0-2) . `evaluate_psi`/`evaluate_f` simply compute `multi_scalar_mul(points, scalars)` over whatever `(point_idx, scalar)` pairs are given [4](#0-3) , and `multi_scalar_mul` is mathematically correct regardless of duplicated bases — it does not "corrupt" the result relative to the honest single-index computation; it *is* the honest computation.

3. **`E_MISMATCHED_LENGTHS` only needs to guard vector-length parity**, not index uniqueness, because uniqueness has no bearing on correctness [5](#0-4) . `sigma_protocol::verify` builds its MSM bases/scalars from these representations and checks equality against the identity point [6](#0-5) ; nothing about repeated indices changes that check's soundness.

Since there is no attacker-reachable path to inject or manipulate `point_idxs`, and repeated indices don't corrupt MSM correctness by design, there is no state-integrity impact here.

### Citations

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_representation.move (L1-12)
```text
module aptos_framework::sigma_protocol_representation {
    friend aptos_framework::sigma_protocol_representation_vec;
    friend aptos_framework::sigma_protocol_homomorphism;
    friend aptos_framework::sigma_protocol;
    friend aptos_framework::sigma_protocol_registration;
    friend aptos_framework::sigma_protocol_withdraw;
    friend aptos_framework::sigma_protocol_transfer;
    friend aptos_framework::sigma_protocol_key_rotation;
    #[test_only]
    friend aptos_framework::sigma_protocol_pedeq_example;
    #[test_only]
    friend aptos_framework::sigma_protocol_schnorr_example;
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_representation.move (L34-39)
```text
    public(friend) fun new_representation(points: vector<u64>, scalars: vector<Scalar>): Representation {
        assert!(points.length() == scalars.length(), error::invalid_argument(E_MISMATCHED_LENGTHS));
        Representation {
            point_idxs: points, scalars
        }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/proofs/sigma_protocol_transfer.move (L372-397)
```text
        // 4. Balance equation: dk · ⟨B, old_R⟩ + (⟨B, new_a⟩ + ⟨B, v⟩) · G
        let idx_old_R_start = START_IDX_OLD_P + ell;
        let point_idxs = vector[];
        let scalars = vector[];

        // dk · B^i · old_R[i]
        vector::range(0, ell).for_each(|i| {
            point_idxs.push_back(idx_old_R_start + i);
            scalars.push_back(dk.scalar_mul(&b_powers_ell[i]));
        });

        // new_a[i] · B^i · G
        vector::range(0, ell).for_each(|i| {
            let new_a_i = *w.get(1 + i);
            point_idxs.push_back(IDX_G);
            scalars.push_back(new_a_i.scalar_mul(&b_powers_ell[i]));
        });

        // v[j] · B^j · G (the secret transfer amount)
        vector::range(0, n).for_each(|j| {
            let v_j = *w.get(1 + 2 * ell + j);
            point_idxs.push_back(IDX_G);
            scalars.push_back(v_j.scalar_mul(&b_powers_n[j]));
        });

        reprs.push_back(new_representation(point_idxs, scalars));
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_homomorphism.move (L67-73)
```text
    #[test_only]
    /// Computes and returns $\psi(X, w) \in \mathbb{G}^m$ given the public statement $X$ and the secret witness $w$.
    public(friend) inline fun evaluate_psi<P>(psi: Homomorphism<P>,
                                   stmt: &Statement<P>,
                                   witn: &Witness): vector<RistrettoPoint> {
        psi(stmt, witn).map_ref(|repr| multi_scalar_mul(&repr.to_points(stmt), repr.get_scalars()))
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol.move (L192-209)
```text
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
