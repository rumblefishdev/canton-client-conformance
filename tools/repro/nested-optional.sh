#!/usr/bin/env bash
# Nested Optional on the JSON Ledger API v2: send Some None in the Daml 2.x form ([]) and in the v2 form ([[]]),
# and None in the 2.x form (null), and print what the ledger stores. Needs curl and jq.
set -u
# Run from the repo root against canton-localnet slot a-validator-1 (see localnet/README.md).
# CLIENT_ID / CLIENT_SECRET: AUTH_A_VALIDATOR_1_VALIDATOR_CLIENT_ID / _SECRET from the canton-localnet checkout's
# compose/modules/keycloak/env/a-validator-1/on/oauth2.env (public demo credentials).
JSON=http://localhost:11975
TOKEN=$(curl -s -d grant_type=client_credentials -d "client_id=$CLIENT_ID" -d "client_secret=$CLIENT_SECRET" \
  -d audience=https://canton.network.global \
  http://localhost:8082/realms/AValidator1/protocol/openid-connect/token | jq -r .access_token)
AUTH="Authorization: Bearer $TOKEN"
curl -s -H "$AUTH" -H 'Content-Type: application/octet-stream' --data-binary @daml/richtypes/richtypes.dar "$JSON/v2/packages" >/dev/null
USER_ID=$(curl -s -H "$AUTH" "$JSON/v2/authenticated-user" | jq -r .user.id)
P=$(curl -s -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"partyIdHint\":\"opt-$RANDOM\",\"identityProviderId\":\"\",\"userId\":\"$USER_ID\"}" "$JSON/v2/parties" | jq -r .partyDetails.party)
# midMaybe : Optional (Optional Text). Send Some None in two encodings and read back what the ledger stored.
for sent in '[[]]' '[]' 'null'; do
  curl -s -H "$AUTH" -H 'Content-Type: application/json' -d "{\"commands\":{\"commands\":[{\"CreateCommand\":{
    \"templateId\":\"#richtypes-h07285b495f9e:RichTypes:NestedOptionalTails\",
    \"createArguments\":{\"owner\":\"$P\",\"midMaybe\":$sent,\"tailMaybe\":[]}}}],
    \"commandId\":\"opt-$RANDOM$RANDOM\",\"actAs\":[\"$P\"]}}" "$JSON/v2/commands/submit-and-wait-for-transaction" \
  | jq -c --arg sent "$sent" '{sent: $sent, stored: .transaction.events[0].CreatedEvent.createArgument.midMaybe, error: .code}'
done
