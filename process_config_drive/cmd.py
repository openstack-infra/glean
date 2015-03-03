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
import platform
import sys

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


def write_redhat_interfaces(interfaces):
    files_to_write = dict()
    for iname, interface in interfaces.items():
        if interface['type'] != 'ipv6':
            interface_name = interface['id'].replace('network', 'eth')
            files_to_write.update(
                _write_rh_interface(interface_name, interface))
    return files_to_write


def write_debian_interfaces(interfaces):
    results = ""
    for iname, interface in interfaces.items():
        link_type = "inet"
        if interface['type'] == 'ipv6':
            link_type = "inet6"
        interface_name = interface['id'].replace('network', 'eth')
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
    return {'/etc/network/interfaces': results}


def write_dns_info(dns_servers):
    results = ""
    for server in dns_servers:
        results += "nameserver {0}\n".format(server)
    return {'/etc/resolv.conf': results}


def write_network_info(net, args):

    dns_servers = [
        f['address'] for f in net['services'] if f['type'] == 'dns'
    ]

    interfaces = {}

    for network in net['networks']:
        interfaces[network['link']] = network
    for link in net['links']:
        interfaces[link['id']]['mac_address'] = link['ethernet_mac_address']

    distro = args.distro
    if not distro:
        distro = platform.dist()[0].lower()
    if distro in ('debian', 'ubuntu'):
        files_to_write = write_debian_interfaces(interfaces)
    elif distro in ('redhat', 'centos', 'fedora', 'suse', 'opensuse'):
        files_to_write = write_redhat_interfaces(interfaces)
    files_to_write.update(write_dns_info(dns_servers))
    for k, v in files_to_write.items():
        if args.noop:
            print("### Write {0}".format(k))
            print(v)
        else:
            with open(k, 'w') as outfile:
                outfile.write(v)


def main():
    parser = argparse.ArgumentParser(description="Static network config")
    parser.add_argument(
        '-n', '--noop', action='store_true', help='Do not write files')
    parser.add_argument(
        '--distro', dest='distro', default=None,
        help='Override detected distro')
    args = parser.parse_args()

    meta_data = json.load(open('/mnt/config/openstack/latest/meta_data.json'))
    with open('/root/.ssh/authorized_keys', 'a') as keys:
        for (name, key) in meta_data['public_keys'].items():
            keys.write("# Injected key {name} by keypair extension\n".format(
                name=name))
            keys.write(key)
            keys.write('\n')

    v = json.load(open('/mnt/config/openstack/latest/vendor_data.json'))
    if 'network_info' in v:
        write_network_info(v['network_info'], args)


if __name__ == '__main__':
    sys.exit(main())
