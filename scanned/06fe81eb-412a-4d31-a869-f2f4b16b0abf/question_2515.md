# Q2515: TransferNFT owner binding against recipient balance

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on token identity preservation, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer around token id validation edge cases and owner index updates, causing `recipient balance` to diverge so the invariant `old owner index removes the token exactly when new owner index adds it` fails and the attacker can use authz/feegrant message construction to authorize transfer from the wrong sender, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: use authz/feegrant message construction to authorize transfer from the wrong sender by testing the owner binding angle against `recipient balance` during `transfer around token id validation edge cases and owner index updates`, with specific focus on token identity preservation.
- Invariant to test: old owner index removes the token exactly when new owner index adds it; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
