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

import argparse
import logging
import os
import subprocess
import sys

log = logging.getLogger("glean-install")


def _find_gleansh_path():
    # the "glean.sh" file is installed in /usr/bin/ on Fedora, and
    # /usr/local/bin on Ubuntu/Debian thanks to differences in pip and
    # where it likes to put scripts.
    if os.path.exists("/usr/local/bin/glean.sh"):
        return "/usr/local/bin"
    if os.path.exists("/usr/bin/glean.sh"):
        return "/usr/bin"
    log.error("Unable to find glean.sh!")
    sys.exit(1)


def install(source_file, target_file, mode='0755', replacements=dict()):
    """Install given SOURCE_FILE to TARGET_FILE with given MODE

    REPLACEMENTS is a dictionary where each KEY will result in the
    template "%%KEY%%" being replaced with its VALUE in TARGET_FILE
    (this is just a sed -i wrapper)

    :param source_file: file to be installed
    :param target_file: location file is being installed to
    :param mode: mode of file being installed
    :param replacements: dict of key/value replacements to the file
    """

    log.info("Installing %s -> %s" % (source_file, target_file))

    script_dir = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), 'init')

    cmd = ('install -D -g root -o root'
           ' -m {mode} {source_file} {target_file}').format(
               source_file=os.path.join(script_dir, source_file),
               target_file=target_file,
               mode=mode)
    log.info(cmd)
    ret = os.system(cmd)
    if ret != 0:
        log.error("Failed to install %s!" % source_file)
        sys.exit(ret)

    for k, v in replacements.items():
        log.info("Replacing %s -> %s in %s" % (k, v, target_file))

        cmd = 'sed -i "s|%%{k}%%|{v}|g" {target_file}'.format(
            k=k, v=v, target_file=target_file)
        log.info(cmd)
        ret = os.system(cmd)

        if ret != 0:
            log.error("Failed to substitute in %s" % target_file)
            sys.exit(ret)


def main():

    parser = argparse.ArgumentParser(
        description='Install glean init components')

    parser.add_argument("-q", "--quiet", help="Be very quiet",
                        action="store_true")

    args = parser.parse_args()

    if args.quiet:
        logging.basicConfig(level=logging.ERROR)
    else:
        logging.basicConfig(level=logging.INFO)

    # needs to go first because gentoo can have systemd along side openrc
    if os.path.exists('/etc/gentoo-release'):
        log.info('installing openrc services')
        install('glean.openrc', '/etc/init.d/glean')
    # Needs to check for the presence of systemd and systemctl
    # as apparently some packages may stage systemd init files
    # when systemd is not present.
    # We also cannot check the path for the init pid as the pid
    # may be wrong as install is generally executed in a chroot
    # with diskimage-builder.

    if (os.path.exists('/usr/lib/systemd/system') and
            (os.path.exists('/usr/bin/systemctl') or
             os.path.exists('/bin/systemctl'))):
        p = _find_gleansh_path()

        log.info("Installing systemd services")
        log.info("glean.sh in %s" % p)

        if os.path.exists('/etc/gentoo-release'):
            install(
                'glean-networkd.service',
                '/lib/systemd/system/glean.service',
                mode='0644',
                replacements={'GLEANSH_PATH': p})
            subprocess.call(['systemctl', 'enable', 'glean.service'])
        else:
            install(
                'glean@.service',
                '/usr/lib/systemd/system/glean@.service',
                mode='0644',
                replacements={'GLEANSH_PATH': p})
        install(
            'glean-udev.rules',
            '/etc/udev/rules.d/99-glean.rules',
            mode='0644')
    elif os.path.exists('/etc/init'):
        log.info("Installing upstart services")
        install('glean.conf', '/etc/init/glean.conf')
    elif os.path.exists('/sbin/rc-update'):
        subprocess.call(['rc-update', 'add', 'glean', 'boot'])
    else:
        log.info("Installing sysv services")
        install('glean.init', '/etc/init.d/glean')
        os.system('update-rc.d glean defaults')

if __name__ == '__main__':
    main()
