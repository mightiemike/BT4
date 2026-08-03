[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-token/sources/token_transfers.move (L199-210)
```text
        let token = pending_claims.remove(token_offer_id);
        let amount = token::get_token_amount(&token);
        token::deposit_token(sender, token);

        event::emit(
            CancelOffer {
                account: sender_addr,
                to_address: receiver,
                token_id,
                amount,
            },
        );
```
