#!/bin/bash -eu

# This script is a modified version of this: https://github.com/canonical/microcloud/blob/main/microcloud/test/main.sh

[ -n "${GOPATH:-}" ] && export "PATH=${GOPATH}/bin:${PATH}"

# Don't translate lxc output for parsing in it in tests.
export LC_ALL="C"

# Force UTC for consistency
export TZ="UTC"

# Tell debconf to not be interactive
export DEBIAN_FRONTEND=noninteractive

export LOG_LEVEL_FLAG=""
if [ -n "${VERBOSE:-}" ]; then
	LOG_LEVEL_FLAG="--verbose"
fi

if [ -n "${DEBUG:-}" ]; then
	LOG_LEVEL_FLAG="--debug"
	set -x
fi

import_subdir_files() {
	test "$1"
	# shellcheck disable=SC3043
	local file
	for file in "$1"/*.sh; do
		# shellcheck disable=SC1090
		. "$file"
	done
}

import_subdir_files includes

echo "==> Checking for dependencies"
check_dependencies lxc lxd curl awk jq git python3 shuf rsync openssl

cleanup() {
	# Do not exit if commands fail on cleanup. (No need to reset -e as this is only run on test suite exit).
	set -eux
	lxc remote switch local
	lxc project switch microcloud-test
	set +e

	if [ -n "${GITHUB_ACTIONS:-}" ]; then
		echo "==> Skipping cleanup (GitHub Action runner detected)"
	else
		echo "==> Cleaning up"

    cleanup_systems
	fi

	echo ""
	echo ""
}

CONCURRENT_SETUP=${CONCURRENT_SETUP:-0}
export CONCURRENT_SETUP

SKIP_SETUP_LOG=${SKIP_SETUP_LOG:-0}
export SKIP_SETUP_LOG

SNAPSHOT_RESTORE=${SNAPSHOT_RESTORE:-0}
export SNAPSHOT_RESTORE

set +u
if [ -z "${MICROCLOUD_SNAP_PATH}" ] || ! [ -e "${MICROCLOUD_SNAP_PATH}" ]; then
    # Update the value of MICROCLOUD_SNAP_PATH to the path of the compiled snap
    MICROCLOUD_SNAP_FOLDER="/home/gab/go/src/github.com/microcloud-pkg-snap"
    MICROCLOUD_SNAP_PATH=$(find "${MICROCLOUD_SNAP_FOLDER}" -name "*.snap")
fi
set -u

export MICROCLOUD_SNAP_PATH

while [[ $# -gt 0 ]]; do
    case "$1" in
        --setup)
            # Create 4 nodes with 3 disks and 3 extra interfaces.
            new_systems 4 3 3
            shift
            ;;
        --nuke)
            cleanup
            shift
            ;;
        --refresh)
            reset_systems 4 3 3
            shift
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done