#!/bin/sh
# Build the plugin on an OPNsense host and install it.
#
#   scripts/deploy.sh user@opnsense-host
#
# Requirements on the OPNsense host:
#   - SSH access with a key, the user may use sudo (member of "admins")
#   - the plugins source tree, once:  sudo opnsense-code plugins   (-> /usr/plugins)
set -eu

TARGET="${1:?usage: $0 user@opnsense-host}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_TMP="/tmp/os-device-onboarding.$$"
PLUGIN_DIR="/usr/plugins/net/device-onboarding"

echo "== copying sources to ${TARGET}:${REMOTE_TMP}"
tar -C "${HERE}" -czf - Makefile pkg-descr src | ssh "${TARGET}" "mkdir -p ${REMOTE_TMP} && tar -C ${REMOTE_TMP} -xzf -"

echo "== building and installing"
ssh -t "${TARGET}" "set -e
  test -d /usr/plugins || { echo 'plugins tree missing, run: sudo opnsense-code plugins'; exit 1; }
  sudo rm -rf ${PLUGIN_DIR} && sudo mkdir -p ${PLUGIN_DIR}
  sudo cp -R ${REMOTE_TMP}/. ${PLUGIN_DIR}/ && rm -rf ${REMOTE_TMP}
  cd ${PLUGIN_DIR} && sudo make clean >/dev/null 2>&1 || true
  sudo make package
  PKG=\$(ls -t ${PLUGIN_DIR}/work/pkg/os-device-onboarding*.pkg | head -1)
  echo \"== installing \$PKG\"
  sudo pkg add -f \"\$PKG\"
  sudo service configd restart
  echo '== done: Services -> Device Onboarding'"
