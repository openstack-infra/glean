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
import subprocess
import sys
import time

post_up = "    post-up route add -net {net} netmask {mask} gw {gw} || true\n"
pre_down = "    pre-down route del -net {net} netmask {mask} gw {gw} || true\n"


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
        interface_name = sys_interfaces[iname]
        files_to_write.update(
            _write_rh_interface(interface_name, interface))
    for mac, iname in sorted(
            sys_interfaces.items(), key=lambda x: x[1]):
        # TODO(mordred) We only want to do this if a file doesn't already exist
        if mac in interfaces:
            # We have a config drive config, move on
            continue
        files_to_write.update(_write_rh_dhcp(iname, mac))
    return files_to_write


def write_debian_interfaces(interfaces, sys_interfaces):
    results = ""
    # Sort the interfaces by id so that we'll have consistent output order
    for iname, interface in sorted(
            interfaces.items(), key=lambda x: x[1]['id']):
        if iname not in sys_interfaces:
            continue
        interface = interfaces[iname]
        link_type = "inet"
        if interface['type'] == 'ipv6':
            link_type = "inet6"
        interface_name = sys_interfaces[iname]
        results += "auto {0}\n".format(interface_name)
        results += "iface {name} {link_type} static\n".format(
            name=interface_name, link_type=link_type)
        results += "    address {0}\n".format(interface['ip_address'])
        results += "    netmask {0}\n".format(interface['netmask'])
        for route in interface['routes']:
            if route['network'] == '0.0.0.0' and route['netmask'] == '0.0.0.0':
                results += "    gateway {0}\n".format(route['gateway'])
            else:
                results += post_up.format(
                    net=route['network'], mask=route['netmask'],
                    gw=route['gateway'])
                results += pre_down.format(
                    net=route['network'], mask=route['netmask'],
                    gw=route['gateway'])
    for mac, iname in sorted(
            sys_interfaces.items(), key=lambda x: x[1]):
        # TODO(mordred) We only want to do this if the interface doesn't
        # already exist
        if mac in interfaces:
            # We have a config drive config, move on
            continue
        results += "auto {0}\n".format(iname)
        results += "iface {0} inet dhcp\n".format(iname)
    return {'/etc/network/interfaces': results}


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
            print("### Write {0}".format(k))
            print(files_to_write[k])
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

    subprocess.check_output(['ip', 'link', 'set', 'dev', iface, 'up'])

    # Poll the interface since it may not come up instantly
    for x in range(0, 10):
        if is_interface_live(iface, sys_root):
            return True
        time.sleep(.1)
    return False


def get_sys_interfaces(interface, args):
    sys_root = '/sys/class/net'
    if args.root != '/mnt/config':  # we're in testing land
        sys_root = args.root + sys_root

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

    network_info_file = '%s/openstack/latest/network_info.json' % args.root
    vendor_data_file = '%s/openstack/latest/vendor_data.json' % args.root

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

    meta_data_path = '%s/openstack/latest/meta_data.json' % args.root
    ssh_path = '%s/root/.ssh' % args.root
    authorized_keys = '%s/authorized_keys' % ssh_path
    if not os.path.exists(meta_data_path):
        return 0
    if not os.path.exists(ssh_path) and not args.noop:
        return 0

    meta_data = json.load(open(meta_data_path))

    keys_to_write = []
    if os.path.exists(authorized_keys) and not args.noop:
        current_keys = open(authorized_keys, 'r').read()
        keys_to_write.append(current_keys)
    else:
        current_keys = ""
        if not args.noop:
            open(authorized_keys, 'w').write(
                "# File created by glean\n")
    for (name, key) in meta_data['public_keys'].items():
        if key not in current_keys or args.noop:
            keys_to_write.append(
                "# Injected key {name} by keypair extension".format(
                    name=name))
            keys_to_write.append(key)
    files_to_write = {
        authorized_keys: '\n'.join(keys_to_write),
    }
    finish_files(files_to_write, args)


def main():
    parser = argparse.ArgumentParser(description="Static network config")
    parser.add_argument(
        '-n', '--noop', action='store_true', help='Do not write files')
    parser.add_argument(
        '--distro', dest='distro', default=None,
        help='Override detected distro')
    parser.add_argument(
        '--root', dest='root', default='/mnt/config',
        help='Mounted root for config drive info, defaults to /mnt/config')
    parser.add_argument(
        '-i', '--interface', dest='interface',
        default=None, help="Interface to process")
    parser.add_argument(
        '--ssh', dest='ssh', action='store_true', help="Write ssh key")
    parser.add_argument(
        '--skip-network', dest='skip', action='store_true',
        help="Do not write network info")
    args = parser.parse_args()
    if args.ssh:
        write_ssh_keys(args)
    if args.interface != 'lo' and not args.skip:
        write_network_info_from_config_drive(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
