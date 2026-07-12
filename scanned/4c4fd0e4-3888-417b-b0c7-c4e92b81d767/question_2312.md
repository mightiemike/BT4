# Q2312: MintNFT owner binding against collection

## Question
Can denom owner or authz grantee enter through `msgServer.MintNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on owner index membership, under the precondition that a valuable denom or NFT exists under the NFT module, then mint under an IBC-prefixed denom and send through nft-transfer, causing `collection` to diverge so the invariant `authz/feegrant cannot widen mint authority beyond the denom owner` fails and the attacker can overwrite an existing token id and seize or burn a victim NFT, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.MintNFT
- Entrypoint: Cosmos SDK MsgMintNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, Name, URI, Data
- Exploit idea: overwrite an existing token id and seize or burn a victim NFT by testing the owner binding angle against `collection` during `mint under an IBC-prefixed denom and send through nft-transfer`, with specific focus on owner index membership.
- Invariant to test: authz/feegrant cannot widen mint authority beyond the denom owner; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over MintNFT, collection indexes, owner indexes, and authz signer path; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
