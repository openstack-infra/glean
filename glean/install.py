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
# See the License for the specific language governing permissions and
# limitations under the License.

import os


def install(source_file, target_file, mode='0755'):
    script_dir = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), 'init')
    os.system(
        'install -D -g root -o root'
        ' -m {mode} {source_file} {target_file}'.format(
            source_file=os.path.join(script_dir, source_file),
            target_file=target_file,
            mode=mode))


def main():

    if os.path.exists('/usr/lib/systemd'):
        install(
            'glean@.service',
            '/usr/lib/systemd/system/glean@.service')
        install(
            'glean-udev.rules',
            '/etc/udev/rules.d/99-glean.rules',
            mode='0644')
    elif os.path.exists('/etc/init'):
        install('glean.conf', '/etc/init/glean.conf')
    else:
        install('glean.init', '/etc/init.d/glean')
        os.system('update-rc.d glean defaults')

if __name__ == '__main__':
    main()
