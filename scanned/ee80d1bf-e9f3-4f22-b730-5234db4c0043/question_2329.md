# Q2329: MintNFT owner binding against NFT owner

## Question
Can denom owner or authz grantee enter through `msgServer.MintNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, Recipient, DenomId, Id, focusing on IBC-prefixed denom separation, under the precondition that a valuable denom or NFT exists under the NFT module, then mint via authz/feegrant path and compare signer to denom owner, causing `NFT owner` to diverge so the invariant `minting cannot create an unbacked IBC voucher NFT` fails and the attacker can mint an NFT in someone else's denom and transfer or bridge it as valuable collateral, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.MintNFT
- Entrypoint: Cosmos SDK MsgMintNFT transaction
- Attacker controls: Sender, Recipient, DenomId, Id, Name, URI, Data
- Exploit idea: mint an NFT in someone else's denom and transfer or bridge it as valuable collateral by testing the owner binding angle against `NFT owner` during `mint via authz/feegrant path and compare signer to denom owner`, with specific focus on IBC-prefixed denom separation.
- Invariant to test: minting cannot create an unbacked IBC voucher NFT; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over MintNFT, collection indexes, owner indexes, and authz signer path; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
