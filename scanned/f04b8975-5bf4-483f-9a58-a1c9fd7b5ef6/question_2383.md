# Q2383: TransferNFT owner binding against collection index

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on denom owner authority, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer then immediately send the same NFT over IBC, causing `collection index` to diverge so the invariant `escrow-owned NFTs cannot be moved by a non-escrow account` fails and the attacker can bypass owner checks with malformed denom or token id storage keys, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: bypass owner checks with malformed denom or token id storage keys by testing the owner binding angle against `collection index` during `transfer then immediately send the same NFT over IBC`, with specific focus on denom owner authority.
- Invariant to test: escrow-owned NFTs cannot be moved by a non-escrow account; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
