# Q2477: TransferNFT owner binding against NFT owner

## Question
Can NFT owner enter through `msgServer.TransferNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on IBC-prefixed denom separation, under the precondition that a valuable denom or NFT exists under the NFT module, then transfer, edit, and burn in reordered transaction sequences, causing `NFT owner` to diverge so the invariant `escrow-owned NFTs cannot be moved by a non-escrow account` fails and the attacker can move an escrowed NFT out of IBC custody and cause later unescrow loss, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.TransferNFT
- Entrypoint: Cosmos SDK MsgTransferNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, transaction ordering
- Exploit idea: move an escrowed NFT out of IBC custody and cause later unescrow loss by testing the owner binding angle against `NFT owner` during `transfer, edit, and burn in reordered transaction sequences`, with specific focus on IBC-prefixed denom separation.
- Invariant to test: escrow-owned NFTs cannot be moved by a non-escrow account; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper/app test asserting owner indexes before and after TransferNFT and IBC send; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
