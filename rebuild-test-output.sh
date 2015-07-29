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


SAMPLE_DIR=glean/tests/fixtures

for vendor_dir in $(find $SAMPLE_DIR \
        -maxdepth 1 -mindepth 1 -type d | grep -v glean/tests/fixtures/test ) ; do
    vendor=$(basename $vendor_dir)
    python glean/cmd.py \
        -n --root $vendor_dir --skip-network --ssh \
        > $SAMPLE_DIR/test/$vendor.keys.out
    for distro in debian ubuntu redhat fedora centos ; do
        python glean/cmd.py \
            -n --root $vendor_dir --distro $distro | python -c 'import sys
skipping = False
for line in sys.stdin.readlines():
    if "eth2" in line:
        skipping = True
        continue
    if line.startswith("### Write"):
        skipping = False
    if skipping:
        continue
    sys.stdout.write(line)
' > $SAMPLE_DIR/test/$vendor.$distro.network.out
    done
done
