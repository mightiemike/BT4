[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_statement.move (L35-42)
```text
    public(friend) fun new_statement<P>(
        points: vector<RistrettoPoint>,
        compressed_points: vector<CompressedRistretto>,
        scalars: vector<Scalar>
    ): Statement<P> {
        assert!(points.length() == compressed_points.length(), error::invalid_argument(E_MISMATCHED_NUMBER_OF_COMPRESSED_POINTS));
        Statement { points, compressed_points, scalars }
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/sigma_protocol_statement_builder.move (L84-87)
```text
    public(friend) fun build<P>(self: StatementBuilder<P>): Statement<P> {
        let StatementBuilder { points, compressed_points, scalars } = self;
        sigma_protocol_statement::new_statement(points, compressed_points, scalars)
    }
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/proofs/sigma_protocol_withdraw.move (L221-234)
```text
        assert!(b.add_point(ristretto255::basepoint_compressed()) == IDX_G, err);                                  // G
        assert!(b.add_point(get_encryption_key_basepoint_compressed()) == IDX_H, err);                             // H
        assert!(b.add_point(compressed_ek) == IDX_EK, err);                                                           // ek
        assert!(b.add_points(compressed_old_balance.get_compressed_P()) == START_IDX_OLD_P, err);                  // old_P
        assert!(b.add_points(compressed_old_balance.get_compressed_R()) == START_IDX_OLD_P + ell, err);            // old_R
        assert!(b.add_points(compressed_new_balance.get_compressed_P()) == START_IDX_OLD_P + 2 * ell, err);        // new_P
        assert!(b.add_points(compressed_new_balance.get_compressed_R()) == START_IDX_OLD_P + 3 * ell, err);        // new_R

        if (compressed_ek_aud.is_some()) {
            assert!(b.add_point(*compressed_ek_aud.borrow()) == START_IDX_OLD_P + 4 * ell, err);                        // ek_aud
            assert!(b.add_points(compressed_new_balance.get_compressed_R_aud()) == START_IDX_OLD_P + 4 * ell + 1, err); // new_R_aud
        };

        assert!(b.add_scalar(v) == IDX_V, err);
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/proofs/sigma_protocol_transfer.move (L284-296)
```text
        assert!(b.add_point(ristretto255::basepoint_compressed()) == IDX_G, e);                                            // G
        assert!(b.add_point(confidential_balance::get_encryption_key_basepoint_compressed()) == IDX_H, e);         // H
        assert!(b.add_point(compressed_ek_sender) == IDX_EK_SENDER, e);                                                       // ek_sender
        assert!(b.add_point(compressed_ek_recip) == IDX_EK_RECIP, e);                                                         // ek_recip
        assert!(b.add_points(compressed_old_balance.get_compressed_P()) == START_IDX_OLD_P, e);                            // old_P
        assert!(b.add_points(compressed_old_balance.get_compressed_R()) == START_IDX_OLD_P + ell, e);                      // old_R
        assert!(b.add_points(compressed_new_balance.get_compressed_P()) == START_IDX_OLD_P + 2 * ell, e); // new_P
        assert!(b.add_points(compressed_new_balance.get_compressed_R()) == START_IDX_OLD_P + 3 * ell, e);                  // new_R
        let (idx, amount_P) = b.add_points_cloned(compressed_amount.get_compressed_P());           // amount_P
        assert!(idx == START_IDX_OLD_P + 4 * ell, e);
        assert!(b.add_points(compressed_amount.get_compressed_R_sender()) == START_IDX_OLD_P + 4 * ell + n, e);            // amount_R_sender
        let (idx, recip_R) = b.add_points_cloned(compressed_amount.get_compressed_R_recip());      // amount_R_recip
        assert!(idx == START_IDX_OLD_P + 4 * ell + 2 * n, e);
```

**File:** aptos-move/framework/aptos-framework/sources/confidential_asset/sigma_protocols/proofs/sigma_protocol_registration.move (L137-143)
```text
        let b = new_builder();
        assert!(b.add_point(get_encryption_key_basepoint_compressed()) == IDX_H, error::internal(E_STATEMENT_BUILDER_INCONSISTENCY)); // H
        assert!(b.add_point(compressed_ek) == IDX_EK, error::internal(E_STATEMENT_BUILDER_INCONSISTENCY)); // ek
        let stmt = b.build();
        assert_registration_statement_is_well_formed(&stmt);
        stmt
    }
```
