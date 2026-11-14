#!/bin/sh
#
# Build the os-device-onboarding package and a signed pkg(8) repository.
# Runs on FreeBSD (CI: GitHub Actions FreeBSD VM) or on an OPNsense host.
#
# usage: build-repo.sh <plugin-source-dir> <output-dir> <rsa-private-key>
#
# Result in <output-dir>:
#   repo/                     signed pkg repository (meta.conf, packagesite.pkg, All/*.pkg)
#   device-onboarding.conf    pkg repository configuration for OPNsense
#   device-onboarding.pub     public key used to verify the repository
#   index.html                installation instructions
#
set -eu

SRC=$(cd "$1" && pwd)
OUT=$2
KEY=$3
BRANCH=${PLUGINS_BRANCH:-stable/26.7}
WORK=$(mktemp -d /tmp/dob-build.XXXXXX)
trap 'rm -rf "$WORK"' EXIT

[ -s "$KEY" ] || { echo "missing signing key: $KEY" >&2; exit 1; }
command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
command -v pkg >/dev/null || { echo "pkg is required" >&2; exit 1; }

echo ">>> Fetching the OPNsense plugins build framework ($BRANCH)"
git clone -q --depth 1 --branch "$BRANCH" https://github.com/opnsense/plugins.git "$WORK/plugins"

echo ">>> Staging plugin sources"
PDIR="$WORK/plugins/net/device-onboarding"
mkdir -p "$PDIR"
cp -R "$SRC/Makefile" "$SRC/pkg-descr" "$SRC/src" "$PDIR/"

echo ">>> Building package"
# PLUGIN_NO_ABI: the plugin contains no compiled code, the package installs
# on every FreeBSD/OPNsense version ("abi: FreeBSD:*:*")
( cd "$PDIR" && make package PLUGIN_NO_ABI=yes )
PKGFILE=$(find "$WORK/plugins" -name 'os-device-onboarding-*.pkg' | head -1)
[ -n "$PKGFILE" ] || { echo "package not found" >&2; exit 1; }
VERSION=$(pkg query -F "$PKGFILE" '%v')
echo ">>> Built $(basename "$PKGFILE") ($(pkg query -F "$PKGFILE" '%q'))"

echo ">>> Creating signed repository"
rm -rf "$OUT"
mkdir -p "$OUT/repo/All"
cp "$PKGFILE" "$OUT/repo/All/"
pkg repo "$OUT/repo" "$KEY"

openssl rsa -in "$KEY" -pubout -out "$OUT/device-onboarding.pub" 2>/dev/null
cp "$SRC/repo/device-onboarding.conf" "$OUT/"
sed -e "s/%%VERSION%%/$VERSION/g" -e "s/%%DATE%%/$(date -u '+%Y-%m-%d')/g" \
    "$SRC/repo/index.html.in" > "$OUT/index.html"
touch "$OUT/.nojekyll"

echo ">>> Done: $OUT"
find "$OUT" -type f | sort
