# LocalNet

Pinned: `peacefulstudio/canton-localnet` **v0.10.2-1** (Splice 0.10.2, Canton 3.7.3). The scenarios use slot
`a-validator-1` (JSON API on `localhost:11975`, gRPC on `localhost:11901`, Keycloak on `localhost:8082`).
The runner records the checkout's `git describe` and the participant's version in every report.

```sh
git clone https://github.com/peacefulstudio/canton-localnet && cd canton-localnet && git checkout v0.10.2-1
make up B_VALIDATOR_1_PROFILE=off C_VALIDATOR_1_PROFILE=off D_VALIDATOR_1_PROFILE=off && make wait-ready
```

In CI the same version runs through the repo's composite GitHub Action (`uses: peacefulstudio/canton-localnet@<sha>`).
