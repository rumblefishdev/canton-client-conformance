#!/usr/bin/env bash
# Fetch DA's prepared-transaction hash vectors (Apache-2.0) at a pinned commit of canton-network/wallet.
set -euo pipefail
REV=${VECTORS_REV:-8b284593f37eb57af2eeabbd36af52ee2b2aaadd}
DIR=$(cd "$(dirname "$0")/.." && pwd)/vectors
rm -rf "$DIR" && tmp=$(mktemp -d)
git -C "$tmp" init -q && git -C "$tmp" remote add origin https://github.com/canton-network/wallet.git
git -C "$tmp" sparse-checkout set core/tx-hashing/vectors
git -C "$tmp" fetch -q --depth 1 origin "$REV" && git -C "$tmp" checkout -q FETCH_HEAD
mv "$tmp/core/tx-hashing/vectors" "$DIR" && echo "$REV" > "$DIR/REV" && rm -rf "$tmp"
echo "vectors at $DIR ($(find "$DIR" -name '*.json' | wc -l) files, rev $REV)"
