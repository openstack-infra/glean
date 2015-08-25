#!/usr/bin/python
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
import json
import os
import platform
import re
import subprocess
import sys
import time

post_up = "    post-up route add -net {net} netmask {mask} gw {gw} || true\n"
pre_down = "    pre-down route del -net {net} netmask {mask} gw {gw} || true\n"


def _exists_rh_interface(name):
    file_to_check = '/etc/sysconfig/network-scripts/ifcfg-{name}'.format(
        name=name
        )
    return os.path.exists(file_to_check)


def _write_rh_interface(name, interface):
    files_to_write = dict()
    results = """# Automatically generated, do not edit
DEVICE={name}
BOOTPROTO=static
HWADDR={hwaddr}
IPADDR={ip_address}
NETMASK={netmask}
ONBOOT=yes
NM_CONTROLLED=no
""".format(
        name=name,
        hwaddr=interface['mac_address'],
        ip_address=interface['ip_address'],
        netmask=interface['netmask'],

    )
    routes = []
    for route in interface['routes']:
        if route['network'] == '0.0.0.0' and route['netmask'] == '0.0.0.0':
            results += "DEFROUTE=yes\n"
            results += "GATEWAY={gw}\n".format(gw=route['gateway'])
        else:
            routes.append(dict(
                net=route['network'], mask=route['netmask'],
                gw=route['gateway']))

    if routes:
        route_content = ""
        for x in range(0, len(routes)):
            route_content += "ADDRESS{x}={net}\n".format(x=x, **routes[x])
            route_content += "NETMASK{x}={mask}\n".format(x=x, **routes[x])
            route_content += "GATEWAY{x}={gw}\n".format(x=x, **routes[x])
        files_to_write['/etc/sysconfig/network-scripts/route-{name}'.format(
            name=name)] = route_content
    files_to_write['/etc/sysconfig/network-scripts/ifcfg-{name}'.format(
        name=name)] = results
    return files_to_write


def _write_rh_dhcp(name, hwaddr):
    filename = '/etc/sysconfig/network-scripts/ifcfg-{name}'.format(name=name)
    return {
        filename: """# Automatically generated, do not edit
DEVICE={name}
BOOTPROTO=dhcp
HWADDR={hwaddr}
ONBOOT=yes
NM_CONTROLLED=no
TYPE=Ethernet
""".format(name=name, hwaddr=hwaddr)
    }


def write_redhat_interfaces(interfaces, sys_interfaces):
    files_to_write = dict()
    # Sort the interfaces by id so that we'll have consistent output order
    for iname, interface in sorted(
            interfaces.items(), key=lambda x: x[1]['id']):
        if interface['type'] == 'ipv6':
            continue
        if iname not in sys_interfaces:
            continue
        if interface['type'] == 'ipv4':
            interface_name = sys_interfaces[iname]
            files_to_write.update(
                _write_rh_interface(interface_name, interface))
        if interface['type'] == 'ipv4_dhcp':
            interface_name = sys_interfaces[iname]
            files_to_write.update(
                _write_rh_dhcp(interface_name, interface['mac_address']))
    for mac, iname in sorted(
            sys_interfaces.items(), key=lambda x: x[1]):
        if _exists_rh_interface(iname):
            # This interface already has a config file, move on
            continue
        if mac in interfaces:
            # We have a config drive config, move on
            continue
        files_to_write.update(_write_rh_dhcp(iname, mac))
    return files_to_write


def _exists_debian_interface(name):
    file_to_check = '/etc/network/interfaces.d/{name}'.format(name=name)
    return os.path.exists(file_to_check)


def write_debian_interfaces(interfaces, sys_interfaces):
    eni_path = '/etc/network/interfaces'
    eni_d_path = eni_path + '.d'
    files_to_write = {}
    files_to_write[eni_path] = "auto lo\niface lo inet loopback\n"
    files_to_write[eni_path] += "source /etc/network/interfaces.d/*.cfg\n"
    # Sort the interfaces by id so that we'll have consistent output order
    for iname, interface in sorted(
            interfaces.items(), key=lambda x: x[1]['id']):
        if iname not in sys_interfaces:
            continue
        interface = interfaces[iname]
        interface_name = sys_interfaces[iname]
        iface_path = os.path.join(eni_d_path, '%s.cfg' % interface_name)

        if interface['type'] == 'ipv4_dhcp':
            result = "auto {0}\n".format(interface_name)
            result += "iface {0} inet dhcp\n".format(interface_name)
            files_to_write[iface_path] = result
            continue
        if interface['type'] == 'ipv6':
            link_type = "inet6"
        elif interface['type'] == 'ipv4':
            link_type = "inet"
        # We do not know this type of entry
        if not link_type:
            continue

        result = "auto {0}\n".format(interface_name)
        result += "iface {name} {link_type} static\n".format(
            name=interface_name, link_type=link_type)
        result += "    address {0}\n".format(interface['ip_address'])
        result += "    netmask {0}\n".format(interface['netmask'])
        for route in interface['routes']:
            if route['network'] == '0.0.0.0' and route['netmask'] == '0.0.0.0':
                result += "    gateway {0}\n".format(route['gateway'])
            else:
                result += post_up.format(
                    net=route['network'], mask=route['netmask'],
                    gw=route['gateway'])
                result += pre_down.format(
                    net=route['network'], mask=route['netmask'],
                    gw=route['gateway'])
        files_to_write[iface_path] = result
    for mac, iname in sorted(
            sys_interfaces.items(), key=lambda x: x[1]):
        if _exists_debian_interface(iname):
            # This interface already has a config file, move on
            continue
        if mac in interfaces:
            # We have a config drive config, move on
            continue
        result = "auto {0}\n".format(iname)
        result += "iface {0} inet dhcp\n".format(iname)
        files_to_write[os.path.join(eni_d_path, "%s.cfg" % iname)] = result
    return files_to_write


def write_dns_info(dns_servers):
    results = ""
    for server in dns_servers:
        results += "nameserver {0}\n".format(server)
    return {'/etc/resolv.conf': results}


def get_config_drive_interfaces(net):
    interfaces = {}

    if 'networks' not in net or 'links' not in net:
        return interfaces

    tmp_ifaces = {}
    for network in net['networks']:
        tmp_ifaces[network['link']] = network
    for link in net['links']:
        tmp_ifaces[link['id']]['mac_address'] = link['ethernet_mac_address']
    for k, v in tmp_ifaces.items():
        v['link'] = k
        interfaces[v['mac_address'].lower()] = v
    return interfaces


def get_dns_from_config_drive(net):
    if 'services' not in net:
        return []
    return [
        f['address'] for f in net['services'] if f['type'] == 'dns'
    ]


def write_static_network_info(
        interfaces, sys_interfaces, files_to_write, args):

    distro = args.distro
    if not distro:
        distro = platform.dist()[0].lower()
    if distro in ('debian', 'ubuntu'):
        files_to_write.update(
            write_debian_interfaces(interfaces, sys_interfaces))
    elif distro in ('redhat', 'centos', 'fedora', 'suse', 'opensuse'):
        files_to_write.update(
            write_redhat_interfaces(interfaces, sys_interfaces))
    else:
        return False

    finish_files(files_to_write, args)


def finish_files(files_to_write, args):
    files = sorted(files_to_write.keys())
    for k in files:
        if not files_to_write[k]:
            # Don't write empty files
            continue
        if args.noop:
            sys.stdout.write("### Write {0}\n{1}".format(k, files_to_write[k]))
        else:
            with open(k, 'w') as outfile:
                outfile.write(files_to_write[k])


def is_interface_live(interface, sys_root):
    try:
        if open('{root}/{iface}/carrier'.format(
                root=sys_root, iface=interface)).read().strip() == '1':
            return True
    except IOError as e:
        # We get this error if the link is not up
        if e.errno != 22:
            raise
    return False


def interface_live(iface, sys_root, args):
    if is_interface_live(iface, sys_root):
        return True

    if args.noop:
        return False

    subprocess.check_call(['ip', 'link', 'set', 'dev', iface, 'up'])

    # Poll the interface since it may not come up instantly
    for x in range(0, 50):
        if is_interface_live(iface, sys_root):
            return True
        time.sleep(.1)
    return False


def get_sys_interfaces(interface, args):
    sys_root = os.path.join(args.root, 'sys/class/net')

    sys_interfaces = {}
    if interface is not None:
        interfaces = [interface]
    else:
        interfaces = [f for f in os.listdir(sys_root) if f != 'lo']
    for iface in interfaces:
        mac_addr_type = open(
            '%s/%s/addr_assign_type' % (sys_root, iface), 'r').read().strip()
        if mac_addr_type != '0':
            continue
        mac = open('%s/%s/address' % (sys_root, iface), 'r').read().strip()
        if interface_live(iface, sys_root, args):
            sys_interfaces[mac] = iface
    return sys_interfaces


def write_network_info_from_config_drive(args):
    """Write network info from config-drive.

    If there is no meta_data.json in config-drive, it means that there
    is no config drive mounted- which means we know nothing.

    Returns False on any issue, which will cause the writing of
    DHCP network files.
    """

    config_drive = os.path.join(args.root, 'mnt/config')
    network_info_file = '%s/openstack/latest/network_info.json' % config_drive
    vendor_data_file = '%s/openstack/latest/vendor_data.json' % config_drive

    v = {}
    if os.path.exists(network_info_file):
        v = json.load(open(network_info_file))
    elif os.path.exists(vendor_data_file):
        vendor_data = json.load(open(vendor_data_file))
        if 'network_info' in vendor_data:
            v = vendor_data['network_info']
    dns = write_dns_info(get_dns_from_config_drive(v))
    interfaces = get_config_drive_interfaces(v)
    sys_interfaces = get_sys_interfaces(args.interface, args)

    write_static_network_info(interfaces, sys_interfaces, dns, args)


def write_ssh_keys(args):
    """Write ssh-keys from config-drive.

    If there is no meta_data.json in config-drive, it means that there
    is no config drive mounted- which means we do nothing.
    """

    config_drive = os.path.join(args.root, 'mnt/config')
    meta_data_path = '%s/openstack/latest/meta_data.json' % config_drive
    if not os.path.exists(meta_data_path):
        return 0

    meta_data = json.load(open(meta_data_path))
    if 'public_keys' not in meta_data:
        return 0

    keys_to_write = []
    for (name, key) in meta_data['public_keys'].items():
        keys_to_write.append(
            "# Injected key {name} by keypair extension".format(
                name=name))
        keys_to_write.append(key)
    files_to_write = {
        '/root/.ssh/authorized_keys': '\n'.join(keys_to_write),
    }
    try:
        os.mkdir('/root/.ssh', 0o700)
    except OSError as e:
        if e.errno == 17:  # File Exists
            pass
        raise
    finish_files(files_to_write, args)


def set_hostname_from_config_drive(args):
    if args.noop:
        return

    config_drive = os.path.join(args.root, 'mnt/config')
    meta_data_path = '%s/openstack/latest/meta_data.json' % config_drive
    if not os.path.exists(meta_data_path):
        return

    meta_data = json.load(open(meta_data_path))
    if 'name' not in meta_data:
        return

    hostname = meta_data['name'].split('.')[0]

    ret = subprocess.call(['hostname', hostname])

    if ret != 0:
        raise RuntimeError('Error setting hostname')
    else:
        with open('/etc/hostname', 'w') as fh:
            fh.write(hostname)
            fh.write('\n')

        # See if we already have a hosts entry for hostname
        prog = re.compile('^127.0.1.1 .*%s' % hostname)
        match = None
        if os.path.isfile('/etc/hosts'):
            with open('/etc/hosts') as fh:
                match = prog.match(fh.read())

        # Write out a hosts entry for hostname
        if match is None:
            with open('/etc/hosts', 'w+') as fh:
                fh.write('127.0.1.1 %s\n' % hostname)


def main():
    parser = argparse.ArgumentParser(description="Static network config")
    parser.add_argument(
        '-n', '--noop', action='store_true', help='Do not write files')
    parser.add_argument(
        '--distro', dest='distro', default=None,
        help='Override detected distro')
    parser.add_argument(
        '--root', dest='root', default='/',
        help='Mounted root for config drive info, defaults to /')
    parser.add_argument(
        '-i', '--interface', dest='interface',
        default=None, help="Interface to process")
    parser.add_argument(
        '--ssh', dest='ssh', action='store_true', help="Write ssh key")
    parser.add_argument(
        '--hostname', dest='hostname', action='store_true',
        help="Set the hostname if name is available in config drive.")
    parser.add_argument(
        '--skip-network', dest='skip', action='store_true',
        help="Do not write network info")
    args = parser.parse_args()
    if args.ssh:
        write_ssh_keys(args)
    if args.hostname:
        set_hostname_from_config_drive(args)
    if args.interface != 'lo' and not args.skip:
        write_network_info_from_config_drive(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
