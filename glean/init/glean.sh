#!/bin/bash
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
#
# See the License for the specific language governing permissions and
# limitations under the License.

# dib-lint: disable=dibdebugtrace
set -eu
set -o pipefail

PATH=/usr/local/bin:/bin:/sbin:/usr/bin:/usr/sbin
INTERFACE=${1:-} #optional, if not specified configure all available interfaces

function config_exists() {
    local interface=$1
    if [ "$CONF_TYPE" == "netscripts" ]; then
        if [ -f "/etc/sysconfig/network-scripts/ifcfg-$interface" ]; then
            return 0
        fi
    # Gentoo: return the value of grep -q INTERFACE in the config file, if it exists
    elif [[ -a /etc/gentoo-release ]]; then
        if [[ -a /etc/conf.d/net ]]; then
            # the '=' is needed so eth0 doesn't match on eth0.1
            grep -q "${interface}=" /etc/conf.d/net* || return 1
        else
            return 1
        fi
    else
        ifquery "${interface}" >/dev/null 2>&1 && return 0 || return 1
    fi
    return 1
}


# Test to see if config-drive exists. If not, skip and assume DHCP networking
# will work because sanity
if blkid -t LABEL="config-2" ; then
    # Mount config drive
    mkdir -p /mnt/config
    BLOCKDEV="$(blkid -L config-2)"
    TYPE="$(blkid -t LABEL=config-2 -s TYPE -o value)"
    if [[ "${TYPE}" == 'vfat' ]]; then
        mount -o umask=0077 "${BLOCKDEV}" /mnt/config || true
    else
        mount -o mode=0700 "${BLOCKDEV}" /mnt/config || true
    fi
    glean --ssh --skip-network --hostname
fi

if [ -n "$INTERFACE" ]; then
    glean --interface "${INTERFACE}"
else
    glean
fi
