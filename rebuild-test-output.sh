#!/bin/bash
# Copyright (c) 2015 Hewlett-Packard Development Company, L.P.
#
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


SAMPLE_DIR=gleam/tests/fixtures

for vendor_dir in $(find $SAMPLE_DIR \
        -maxdepth 1 -mindepth 1 -type d | grep -v test) ; do
    vendor=$(basename $vendor_dir)
    python gleam/cmd.py \
        -n --root $vendor_dir --skip-network --ssh \
        > $SAMPLE_DIR/test/$vendor.keys.out
    for distro in debian ubuntu redhat fedora centos ; do
        python gleam/cmd.py \
            -n --root $vendor_dir --distro $distro > $SAMPLE_DIR/test/$vendor.$distro.network.out
    done
done
