#!/bin/bash

MICROCLOUD_SOURCE_PATH="/home/gab/go/src/github.com/microcloud"
MICROCLOUD_SNAP_FOLDER="/home/gab/go/src/github.com/microcloud-pkg-snap"

# rsync the microcloud local source to the microcloud-pkg-snap directory
rsync -r "${MICROCLOUD_SOURCE_PATH}" "${MICROCLOUD_SNAP_FOLDER}/microcloud"

# remove any .snap files in the microcloud-pkg-snap directory
rm -rf "${MICROCLOUD_SNAP_FOLDER}/*.snap"

# Edit the snapcraft.yaml file to include the microcloud local source
sed -i '/microcloud:/,/^$/{
    /source:/c\    source: ./microcloud
    /source-type:/c\    source-type: local
    /source-depth:/d
}' "${MICROCLOUD_SNAP_FOLDER}/snapcraft.yaml"

# Compile the microcloud snap
cd "${MICROCLOUD_SNAP_FOLDER}" && snapcraft

# Remove the microcloud folder from the microcloud-pkg-snap directory
rm -rf "${MICROCLOUD_SNAP_FOLDER}/microcloud"

# Revert the specified section in the snapcraft.yaml file to its original state
sed -i '/microcloud:/,/^$/{
    /source:/c\    source: https://github.com/canonical/microcloud
    /source-type:/c\    source-type: git\n\t\tsource-depth: 1
}' "${MICROCLOUD_SNAP_FOLDER}/snapcraft.yaml"