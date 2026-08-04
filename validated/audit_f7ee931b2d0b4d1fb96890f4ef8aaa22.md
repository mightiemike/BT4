[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_representation_vec.move (L54-59)
```text
    /// For all $i$, multiply the $i$th representation by `b[i]` (i.e., multiply `self.v[i].scalars` by `b[i]`)
    public(friend) fun scale_each(self: &mut RepresentationVec, b: &vector<Scalar>) {
        self.v.enumerate_mut(|i, repr| {
            repr.scale(&b[i])
        });
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/proofs/sigma_protocol_transfer.move (L328-336)
```text
        // WARNING: Crucial for security
        assert_transfer_statement_is_well_formed(stmt, has_effective_auditor, num_volun_auditors);

        let ell = get_ell();
        let n = get_n();

        // WARNING: Crucial for security
        let expected_witness_len = 1 + 2 * ell + 2 * n;
        assert!(w.length() == expected_witness_len, e_wrong_witness_len());
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/proofs/sigma_protocol_transfer.move (L443-446)
```text
        // WARNING: Crucial for security
        assert!(reprs.length() == expected_output_len(ell, n, has_effective_auditor, num_volun_auditors), e_wrong_output_len());
        new_representation_vec(reprs)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol.move (L164-180)
```text
        let psi_sigma = psi(stmt, &proof.response_to_witness());
        let efx = f(stmt);

        assert!(m == psi_sigma.length(), error::invalid_argument(E_PROOF_COMMITMENT_WRONG_LEN));
        assert!(m == efx.length(), error::invalid_argument(E_PROOF_COMMITMENT_WRONG_LEN));

        // "Scale" all the representations in `f(stmt)` by `e`. (Implicit assumption here is that `f` is homomorphic:
        // i.e., `e f(X) = f(eX)`, which holds because our `f`'s are a `RepresentationVec`.)
        efx.scale_all(&e);

        // "Scale" the `i`th reprentation in `efx` by `\beta[i]`
        efx.scale_each(&betas);

        // "Scale" the `i`th reprentation in `\psi` by `-\beta[i]`
        // TODO(Perf): I think this could be sub-optimal: we will redo the same \beta[i] \sigma[j] multiplication several times
        //   when a `RepresentationVec`'s row reuses \sigma[j].
        psi_sigma.scale_each(&neg_scalars(&betas));
```
