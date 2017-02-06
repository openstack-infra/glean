#!/usr/bin/python
# Copyright (c) 2015 Monty Taylor
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
import contextlib
import copy
import errno
import json
import logging
import os
import platform
import re
import subprocess
import sys
import time

from glean import systemlock
from glean import utils

log = logging.getLogger("glean")

slaves_add = "    post-up ifenslave {0} {1}\n"
slaves_del = "    pre-down ifenslave -d {0} {1}\n"


# Type value for permanent mac addrs as defined by the linux kernel.
PERMANENT_ADDR_TYPE = '0'

# Global flag for selinux restore.
SELINUX_RESTORECON = '/usr/sbin/restorecon'
HAVE_SELINUX = os.path.exists(SELINUX_RESTORECON)


# Wrap open calls in this to make sure that any created or modified
# files retain their selinux context.
@contextlib.contextmanager
def safe_open(*args, **kwargs):
    f = open(*args, **kwargs)
    try:
        yield f
    finally:
        path = os.path.abspath(f.name)
        if HAVE_SELINUX:
            logging.debug("Restoring selinux context for %s" % path)
            subprocess.call([SELINUX_RESTORECON, path])


def _exists_rh_interface(name):
    file_to_check = '/etc/sysconfig/network-scripts/ifcfg-{name}'.format(
        name=name
        )
    return os.path.exists(file_to_check)


def _is_suse(distro):
    return distro in ('suse', 'opensuse')


def _network_files(distro):
    network_files = {}
    if _is_suse(distro):
        # network.service is an alias to wicked.service on SUSE
        # and openSUSE images so use that instead of network.service
        # since systemd refuses to treat aliases as normal services
        network_files = {
            "systemd": "wicked.service",
            "ifcfg": "/etc/sysconfig/network/ifcfg",
            "route": "/etc/sysconfig/network/ifroute",
        }
    else:
        network_files = {
            "systemd": "network.service",
            "ifcfg": "/etc/sysconfig/network-scripts/ifcfg",
            "route": "/etc/sysconfig/network-scripts/route",
        }

    return network_files


def _network_config(distro):
    network_config = {}
    if _is_suse(distro):
        header = "\n".join(["# Automatically generated, do not edit",
                            "BOOTPROTO={bootproto}",
                            "LLADDR={hwaddr}"])
        footer = "STARTMODE=auto" + "\n"

        network_config = {
            "static": "\n".join([header,
                                 "IPADDR={ip_address}",
                                 "NETMASK={netmask}",
                                 footer])
        }
    else:
        header = "\n".join(["# Automatically generated, do not edit",
                            "DEVICE={name}",
                            "BOOTPROTO={bootproto}",
                            "HWADDR={hwaddr}"])
        footer = "\n".join(["ONBOOT=yes", "NM_CONTROLLED=no",
                            "TYPE=Ethernet"]) + "\n"

        network_config = {
            # RedHat does not use TYPE=Ethernet in the static configurations
            "static": "\n".join([header,
                                 "IPADDR={ip_address}",
                                 "NETMASK={netmask}",
                                 footer.replace("TYPE=Ethernet\n", "")])
        }

    # RedHat does not use TYPE=Ethernet in the dhcp configurations
    network_config["dhcp"] = "\n".join([header, footer])
    network_config["none"] = "\n".join([header, footer])

    return network_config


def _set_rh_bonding(name, interface, distro, results):
    if not any(bond in ['bond_slaves', 'bond_master'] for bond in interface):
        return results

    # Careful, we are operating on the live 'results' variable
    # so we need to always append our data
    if _is_suse(distro):
        # SUSE configures the slave interfaces on the master ifcfg file.
        # The master interface contains a 'bond_slaves' key containing a list
        # of the slave interfaces
        if 'bond_slaves' in interface:
            results += "BONDING_MASTER=yes\n"
            slave_cnt = 0
            for slave in interface['bond_slaves']:
                results += "BONDING_SLAVE_{id}={name}\n".format(
                    id=slave_cnt, name=slave)
                slave_cnt += 1
        else:
            # Slave interfaces do not know they are part of a bonded
            # interface. All we need to do is to set the STARTMODE
            # to hotplug
            results = results.replace("=auto", "=hotplug")

    else:
        # RedHat does not add any specific configuration to the master
        # interface. All configuration is done in the slave ifcfg files.
        if 'bond_slaves' in interface:
            return results

        results += "SLAVE=yes\n"
        results += "MASTER={0}\n".format(interface['bond_master'])

    return results


def _set_rh_vlan(name, interface, distro):
    results = ""

    if 'vlan_id' not in interface:
        return results

    if _is_suse(distro):
        results += "VLAN_ID={vlan_id}\n".format(vlan_id=interface['vlan_id'])
        results += "ETHERDEVICE={etherdevice}\n".format(
            etherdevice=name.split('.')[0])
    else:
        results += "VLAN=yes\n"

    return results


def _write_rh_interface(name, interface, distro):
    files_to_write = dict()
    results = _network_config(distro)["static"].format(
        bootproto="static",
        name=name,
        hwaddr=interface['mac_address'],
        ip_address=interface['ip_address'],
        netmask=interface['netmask'],

    )
    results += _set_rh_vlan(name, interface, distro)
    # set_rh_bonding takes results as argument so we need to assign
    # the return value, not append it
    results = _set_rh_bonding(name, interface, distro, results)
    routes = []
    for route in interface['routes']:
        if route['network'] == '0.0.0.0' and route['netmask'] == '0.0.0.0':
            if not _is_suse(distro):
                results += "DEFROUTE=yes\n"
                results += "GATEWAY={gw}\n".format(gw=route['gateway'])
            else:
                # Special notation for default route on SUSE/wicked
                routes.append(dict(
                    net='default', mask='', gw=route['gateway']))
        else:
            routes.append(dict(
                net=route['network'], mask=route['netmask'],
                gw=route['gateway']))

    if routes:
        route_content = ""
        for x in range(0, len(routes)):
            if not _is_suse(distro):
                route_content += "ADDRESS{x}={net}\n".format(x=x, **routes[x])
                route_content += "NETMASK{x}={mask}\n".format(x=x, **routes[x])
                route_content += "GATEWAY{x}={gw}\n".format(x=x, **routes[x])
            else:
                # Avoid the extra trailing whitespace for the default route
                # because mask is empty in that case.
                route_content += "{net} {gw} {mask}\n".format(
                    **routes[x]).replace(' \n', '\n')
        files_to_write[_network_files(distro)["route"] + '-{name}'
                       .format(name=name)] = route_content
    files_to_write[_network_files(distro)["ifcfg"] + '-{name}'.format(
        name=name)] = results

    return files_to_write


def _write_rh_dhcp(name, interface, distro):
    filename = _network_files(distro)["ifcfg"] + '-{name}'.format(name=name)
    results = _network_config(distro)["dhcp"].format(
        bootproto="dhcp", name=name, hwaddr=interface['mac_address'])
    results += _set_rh_vlan(name, interface, distro)
    # set_rh_bonding takes results as argument so we need to assign
    # the return value, not append it
    results = _set_rh_bonding(name, interface, distro, results)

    return {filename: results}


def _write_rh_manual(name, interface, distro):
    filename = _network_files(distro)["ifcfg"] + '-{name}'.format(name=name)
    results = _network_config(distro)["none"].format(
        bootproto="none", name=name, hwaddr=interface['mac_address'])
    results += _set_rh_vlan(name, interface, distro)
    # set_rh_bonding takes results as argument so we need to assign
    # the return value, not append it
    results = _set_rh_bonding(name, interface, distro, results)

    return {filename: results}


def write_redhat_interfaces(interfaces, sys_interfaces, distro):
    files_to_write = dict()
    # Sort the interfaces by id so that we'll have consistent output order
    for iname, interface in sorted(
            interfaces.items(), key=lambda x: x[1]['id']):
        if interface['type'] == 'ipv6':
            continue
        # sys_interfaces is pruned by --interface; if one of the
        # raw_macs (or, *the* MAC for single interfaces) does not
        # match as one of the interfaces we want configured, skip
        raw_macs = interface.get('raw_macs', [interface['mac_address']])
        if not set(sys_interfaces).intersection(set(raw_macs)):
            continue

        if 'vlan_id' in interface:
            # raw_macs will have a single entry if the vlan device is a
            # phsical device and >1 when it is a bond device.
            if len(raw_macs) == 1:
                vlan_raw_device = sys_interfaces.get(raw_macs[0])
            else:
                vlan_raw_device = interface['vlan_link']
            interface_name = "{0}.{1}".format(
                vlan_raw_device, interface['vlan_id'])
        elif 'bond_mode' in interface:
            # It is possible our interface does not have a link, so fall back
            # to iname which is the link id.
            interface_name = interface.get('link', iname)
        else:
            interface_name = sys_interfaces[interface['mac_address']]

        if 'bond_links' in interface:
            # We need to keep track of the slave interfaces because
            # SUSE configures the slaves on the master ifcfg file
            bond_slaves = []
            for phy in interface['raw_macs']:
                bond_slaves.append(sys_interfaces[phy])
            interface['bond_slaves'] = bond_slaves
            # Remove the 'bond_links' key
            interface.pop('bond_links')

        if interface['type'] == 'ipv4':
            files_to_write.update(
                _write_rh_interface(interface_name, interface, distro))
        if interface['type'] == 'ipv4_dhcp':
            files_to_write.update(
                _write_rh_dhcp(interface_name, interface, distro))
        if interface['type'] == 'manual':
            files_to_write.update(
                _write_rh_manual(interface_name, interface, distro))
    for mac, iname in sorted(
            sys_interfaces.items(), key=lambda x: x[1]):
        if _exists_rh_interface(iname):
            # This interface already has a config file, move on
            log.debug("%s already has config file, skipping" % iname)
            continue
        inter_macs = [intf['mac_address'] for intf in interfaces.values()]
        link_macs = [intf.get('link_mac') for intf in interfaces.values()
                     if 'vlan_id' in interface]
        if mac in inter_macs or mac in link_macs:
            # We have a config drive config, move on
            log.debug("%s configured via config-drive" % mac)
            continue
        files_to_write.update(_write_rh_dhcp(iname, {'mac_address': mac},
                                             distro))
    return files_to_write


def _exists_gentoo_interface(name):
    file_to_check = '/etc/conf.d/net.{name}'.format(name=name)
    return os.path.exists(file_to_check)


def _enable_gentoo_interface(name):
    log.debug('rc-update add {name} default'.format(name=name))
    subprocess.call(['rc-update', 'add',
                     'net.{name}'.format(name=name), 'default'])


def _write_gentoo_interface(name, interfaces):
    files_to_write = dict()
    results = ""
    vlans = []
    for interface in interfaces:
        iname = name
        if 'vlan_id' in interface:
            vlans.append(interface['vlan_id'])
            iname = "%s_%s" % (iname, interface['vlan_id'])
        if interface['type'] == 'ipv4':
            results += """config_{name}="{ip_address} netmask {netmask}"
mac_{name}="{hwaddr}\"\n""".format(
                name=iname,
                ip_address=interface['ip_address'],
                netmask=interface['netmask'],
                hwaddr=interface['mac_address']
            )
            routes = list()
            for route in interface['routes']:
                if (route['network'] == '0.0.0.0' and
                        route['netmask'] == '0.0.0.0'):
                    # add default route if it exists
                    routes.append('default via {gw}'.format(
                        name=name,
                        gw=route['gateway']
                    ))
                else:
                    # add remaining static routes
                    routes.append('{net} netmask {mask} via {gw}'.format(
                        net=route['network'],
                        mask=route['netmask'],
                        gw=route['gateway']
                    ))
            if routes:
                routes_string = '\n'.join(route for route in routes)
                results += 'routes_{name}="{routes}"'.format(
                    name=name,
                    routes=routes_string
                    # routes='\n'.join(str(route) for route in routes)
                )
                results += '\n'
        elif interface['type'] == 'manual':
            results += """config_{name}="null"
mac_{name}="{hwaddr}"
""".format(name=iname, hwaddr=interface['mac_address'])
            _enable_gentoo_interface(iname)
        else:
            results += """config_{name}="dhcp"
mac_{name}="{hwaddr}"
""".format(name=iname, hwaddr=interface['mac_address'])
            _enable_gentoo_interface(iname)
        if 'bond_mode' in interface:
            slaves = ' '.join(interface['slaves'])
            results += """slaves_{name}="{slaves}"
mode_{name}="{mode}"
""".format(name=iname, slaves=slaves, mode=interface['bond_mode'])

    full_results = "# Automatically generated, do not edit\n"
    if vlans:
        full_results += 'vlans_{name}="{vlans}"\n'.format(
            name=name,
            vlans=' '.join(str(vlan) for vlan in vlans))
    full_results += results

    files_to_write['/etc/conf.d/net.{name}'.format(name=name)] = full_results
    return files_to_write


def _setup_gentoo_network_init(sys_interface, interfaces):
    for interface in interfaces:
        interface_name = '{name}'.format(name=sys_interface)
        if 'vlan_id' in interface:
            interface_name += ".{vlan}".format(
                vlan=interface['vlan_id'])
            log.debug('vlan {vlan} found, interface named {name}'.
                      format(vlan=interface['vlan_id'], name=interface_name))
        if 'bond_master' in interface:
            continue
        _create_gentoo_net_symlink_and_enable(interface_name)
    if not interfaces:
        _create_gentoo_net_symlink_and_enable(sys_interface)


def _create_gentoo_net_symlink_and_enable(interface_name):
    file_path = '/etc/init.d/net.{name}'.format(name=interface_name)
    if not os.path.islink(file_path):
        log.debug('ln -s /etc/init.d/net.lo {file_path}'.
                  format(file_path=file_path))
        os.symlink('/etc/init.d/net.lo',
                   '{file_path}'.format(file_path=file_path))
        _enable_gentoo_interface(interface_name)


def write_gentoo_interfaces(interfaces, sys_interfaces):
    files_to_write = dict()
    gen_intfs = {}
    # Sort the interfaces by id so that we'll have consistent output order
    for iname, interface in sorted(
            interfaces.items(), key=lambda x: x[1]['id']):
        if interface['type'] == 'ipv6':
            continue
        # sys_interfaces is pruned by --interface; if one of the
        # raw_macs (or, *the* MAC for single interfaces) does not
        # match as one of the interfaces we want configured, skip
        raw_macs = interface.get('raw_macs', [interface['mac_address']])
        if not set(sys_interfaces).intersection(set(raw_macs)):
            continue

        if 'bond_mode' in interface:
            interface['slaves'] = [
                sys_interfaces[mac] for mac in interface['raw_macs']]

        if 'raw_macs' in interface:
            key = tuple(interface['raw_macs'])
            if key not in gen_intfs:
                gen_intfs[key] = []
            gen_intfs[key].append(interface)
        else:
            key = (interface['mac_address'],)
            if key not in gen_intfs:
                gen_intfs[key] = []
            gen_intfs[key].append(interface)

    for raw_macs, interfs in gen_intfs.items():
        if len(raw_macs) == 1:
            interface_name = sys_interfaces[raw_macs[0]]
        else:
            # It is possible our interface does not have a link, so
            # fall back to interface id.
            interface_name = next(
                intf.get('link', intf['id']) for intf in interfs
                if 'bond_mode' in intf)
        files_to_write.update(
            _write_gentoo_interface(interface_name, interfs))
        _setup_gentoo_network_init(interface_name, interfs)

    for mac, iname in sorted(
            sys_interfaces.items(), key=lambda x: x[1]):
        if _exists_gentoo_interface(iname):
            # This interface already has a config file, move on
            log.debug("%s already has config file, skipping" % iname)
            continue
        if (mac,) in gen_intfs:
            # We have a config drive config, move on
            log.debug("%s configured via config-drive" % mac)
            continue
        interface = {'type': 'ipv4_dhcp', 'mac_address': mac}
        files_to_write.update(_write_gentoo_interface(iname, [interface]))
        _setup_gentoo_network_init(iname, [])
    return files_to_write


def systemd_enable(service, args):
    log.debug("Enabling %s via systemctl" % service)

    if args.noop:
        return

    rc = os.system('systemctl enable %s' % service)
    if rc != 0:
        log.error("Error enabling %s" % service)
        sys.exit(rc)


def _exists_debian_interface(name):
    file_to_check = '/etc/network/interfaces.d/{name}.cfg'.format(name=name)
    return os.path.exists(file_to_check)


def write_debian_interfaces(interfaces, sys_interfaces):
    eni_path = '/etc/network/interfaces'
    eni_d_path = eni_path + '.d'
    files_to_write = dict()
    files_to_write[eni_path] = "auto lo\niface lo inet loopback\n"
    files_to_write[eni_path] += "source /etc/network/interfaces.d/*.cfg\n"
    # Sort the interfaces by id so that we'll have consistent output order
    for iname, interface in sorted(
            interfaces.items(), key=lambda x: x[1]['id']):
        # sys_interfaces is pruned by --interface; if one of the
        # raw_macs (or, *the* MAC for single interfaces) does not
        # match as one of the interfaces we want configured, skip
        raw_macs = interface.get('raw_macs', [interface['mac_address']])
        if not set(sys_interfaces).intersection(set(raw_macs)):
            continue

        vlan_raw_device = None
        if 'vlan_id' in interface:
            # raw_macs will have a single entry if the vlan device is a
            # phsical device and >1 when it is a bond device.
            if len(raw_macs) == 1:
                vlan_raw_device = sys_interfaces.get(raw_macs[0])
            else:
                vlan_raw_device = interface['vlan_link']
            interface_name = "{0}.{1}".format(vlan_raw_device,
                                              interface['vlan_id'])
        elif 'bond_mode' in interface:
            # It is possible our interface does not have a link, so fall back
            # to iname which is the link id.
            interface_name = interface.get('link', iname)
        else:
            interface_name = sys_interfaces[interface['mac_address']]

        if _exists_debian_interface(interface_name):
            continue

        iface_path = os.path.join(eni_d_path, '%s.cfg' % interface_name)

        if interface['type'] == 'ipv4_dhcp':
            header = "auto {0}\n".format(interface_name)
            result = "iface {0} inet dhcp\n".format(interface_name)
            if vlan_raw_device is not None:
                result += "    vlan-raw-device {0}\n".format(vlan_raw_device)
                result += "    hw-mac-address {0}\n".format(
                    interface['mac_address'])
            if 'bond_mode' in interface:
                if interface['mac_address']:
                    result += "    hwaddress {0}\n".format(
                        interface['mac_address'])
                result += "    bond-mode {0}\n".format(interface['bond_mode'])
                result += "    bond-miimon {0}\n".format(
                    interface.get('bond_miimon', 0))
                result += "    bond-lacp-rate {0}\n".format(
                    interface.get('bond_lacp_rate', 'slow'))
                result += "    bond-xmit_hash_policy {0}\n".format(
                    interface.get('bond_xmit_hash_policy', 'layer2'))
                slave_devices = [sys_interfaces[mac]
                                 for mac in interface['raw_macs']]
                slaves = ' '.join(slave_devices)
                result += "    bond-slaves none\n"
                result += slaves_add.format(interface_name, slaves)
                result += slaves_del.format(interface_name, slaves)
            if iface_path in files_to_write:
                # There are more than one address for this interface
                files_to_write[iface_path] += result
            else:
                files_to_write[iface_path] = header + result
            continue

        if interface['type'] == 'manual':
            header = "auto {0}\n".format(interface_name)
            result = "iface {0} inet manual\n".format(interface_name)
            if 'bond_master' in interface:
                result += "    bond-master {0}\n".format(
                    interface['bond_master'])
            if 'bond_mode' in interface:
                if interface['mac_address']:
                    result += "    hwaddress {0}\n".format(
                        interface['mac_address'])
                result += "    bond-mode {0}\n".format(interface['bond_mode'])
                result += "    bond-miimon {0}\n".format(
                    interface.get('bond_miimon', 0))
                result += "    bond-lacp-rate {0}\n".format(
                    interface.get('bond_lacp_rate', 'slow'))
                result += "    bond-xmit_hash_policy {0}\n".format(
                    interface.get('bond_xmit_hash_policy', 'layer2'))
                slave_devices = [sys_interfaces[mac]
                                 for mac in interface['raw_macs']]
                slaves = ' '.join(slave_devices)
                result += "    bond-slaves none\n"
                result += slaves_add.format(interface_name, slaves)
                result += slaves_del.format(interface_name, slaves)
            if iface_path in files_to_write:
                # There are more than one address for this interface
                files_to_write[iface_path] += result
            else:
                files_to_write[iface_path] = header + result
            continue

        if interface['type'] == 'ipv6':
            link_type = "inet6"
        elif interface['type'] == 'ipv4':
            link_type = "inet"

        # We do not know this type of entry
        if not link_type:
            continue

        header = "auto {0}\n".format(interface_name)
        result = "iface {name} {link_type} static\n".format(
            name=interface_name, link_type=link_type)
        if vlan_raw_device:
            result += "    vlan-raw-device {0}\n".format(vlan_raw_device)
        if 'bond_master' in interface:
            result += "    bond-master {0}\n".format(
                interface['bond_master'])
        if 'bond_mode' in interface:
            if interface['mac_address']:
                result += "    hwaddress {0}\n".format(
                    interface['mac_address'])
            result += "    bond-mode {0}\n".format(interface['bond_mode'])
            result += "    bond-miimon {0}\n".format(
                interface.get('bond_miimon', 0))
            result += "    bond-lacp-rate {0}\n".format(
                interface.get('bond_lacp_rate', 'slow'))
            result += "    bond-xmit_hash_policy {0}\n".format(
                interface.get('bond_xmit_hash_policy', 'layer2'))
            slave_devices = [sys_interfaces[mac]
                             for mac in interface['raw_macs']]
            slaves = ' '.join(slave_devices)
            result += "    bond-slaves none\n"
            result += slaves_add.format(interface_name, slaves)
            result += slaves_del.format(interface_name, slaves)
        result += "    address {0}\n".format(interface['ip_address'])

        if interface['type'] == 'ipv4':
            result += "    netmask {0}\n".format(interface['netmask'])
        else:
            result += "    netmask {0}\n".format(
                utils.ipv6_netmask_length(interface['netmask']))

        for route in interface['routes']:
            if ((route['network'] == '0.0.0.0' and
                    route['netmask'] == '0.0.0.0') or
                (route['network'] == '::' and
                    route['netmask'] == '::')):
                result += "    gateway {0}\n".format(route['gateway'])
            else:
                if interface['type'] == 'ipv4':
                    route_add = ("    up route add -net {net} netmask "
                                 "{mask} gw {gw} || true\n")
                    route_del = ("    down route del -net {net} netmask "
                                 "{mask} gw {gw} || true\n")
                    _netmask = route['netmask']
                else:
                    route_add = ("    up ip -6 route add {net}/{mask} "
                                 "via {gw} dev {interface} || true\n")
                    route_del = ("    down ip -6 route del {net}/{mask} "
                                 "via {gw} dev {interface} || true\n")
                    _netmask = utils.ipv6_netmask_length(route['netmask'])

                result += route_add.format(
                    net=route['network'], mask=_netmask, gw=route['gateway'],
                    interface=interface_name)
                result += route_del.format(
                    net=route['network'], mask=_netmask, gw=route['gateway'],
                    interface=interface_name)

        if iface_path in files_to_write:
            # There are more than one address for this interface
            files_to_write[iface_path] += result
        else:
            files_to_write[iface_path] = header + result

    for mac, iname in sorted(
            sys_interfaces.items(), key=lambda x: x[1]):
        if _exists_debian_interface(iname):
            # This interface already has a config file, move on
            continue
        inter_macs = [intf['mac_address'] for intf in interfaces.values()]
        link_macs = [intf.get('link_mac') for intf in interfaces.values()
                     if 'vlan_id' in interface]
        if mac in inter_macs or mac in link_macs:
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
        log.debug("No config-drive interfaces defined")
        return interfaces

    networks = {}
    for network in net['networks']:
        networks[network['link']] = network

    vlans = {}
    phys = {}
    bonds = {}
    for link in net['links']:
        if link['type'] == 'vlan':
            vlans[link['id']] = link
        elif link['type'] == 'bond':
            bonds[link['id']] = link
        else:
            phys[link['id']] = link

    for link in vlans.values():
        if link['vlan_link'] in phys:
            vlan_link = phys[link['vlan_link']]
            link['raw_macs'] = [vlan_link['ethernet_mac_address'].lower()]
        elif link['vlan_link'] in bonds:
            vlan_link = bonds[link['vlan_link']]
            link['raw_macs'] = []
            for phy in vlan_link['bond_links']:
                link['raw_macs'].append(
                    phys[phy]['ethernet_mac_address'].lower())
        link['mac_address'] = link.pop(
            'vlan_mac_address', vlan_link['ethernet_mac_address']).lower()

    for link in bonds.values():
        phy_macs = []
        for phy in link['bond_links']:
            phy_link = phys[phy]
            phy_link['bond_master'] = link['id']
            if phy in phys:
                phy_macs.append(phy_link['ethernet_mac_address'].lower())
        link['raw_macs'] = phy_macs
        link['mac_address'] = link.pop('ethernet_mac_address').lower()
        if link['id'] not in networks:
            link['type'] = 'manual'
            interfaces[link['id']] = link

    for link in phys.values():
        link['mac_address'] = link.pop('ethernet_mac_address').lower()
        if link['id'] not in networks:
            link['type'] = 'manual'
            interfaces[link['id']] = link

    for network in net['networks']:
        link = vlans.get(
            network['link'],
            phys.get(network['link'], bonds.get(network['link'])))
        if not link:
            continue

        link.update(network)
        # NOTE(pabelanger): Make sure we index by the existing network id,
        # rather then creating out own.
        interfaces[network['id']] = copy.deepcopy(link)

    return interfaces


def get_dns_from_config_drive(net):
    if 'services' not in net:
        log.debug("No DNS info available from config-drive")
        return []
    return [
        f['address'] for f in net['services'] if f['type'] == 'dns'
    ]


def write_static_network_info(
        interfaces, sys_interfaces, files_to_write, args):

    if args.distro in ('debian', 'ubuntu'):
        files_to_write.update(
            write_debian_interfaces(interfaces, sys_interfaces))
    elif args.distro in ('redhat', 'centos', 'fedora', 'suse', 'opensuse'):
        files_to_write.update(
            write_redhat_interfaces(interfaces, sys_interfaces, args.distro))

        # glean configures interfaces via
        # /etc/sysconfig/network-scripts, so we have to ensure that
        # the LSB init script /etc/init.d/network gets started!
        systemd_enable(_network_files(args.distro)["systemd"], args)
    elif args.distro in 'gentoo':
        files_to_write.update(
            write_gentoo_interfaces(interfaces, sys_interfaces)
        )
    else:
        return False

    finish_files(files_to_write, args)


def finish_files(files_to_write, args):
    files = sorted(files_to_write.keys())
    log.debug("Writing output files")
    for k in files:
        if not files_to_write[k]:
            # Don't write empty files
            log.debug("%s is blank, skipped" % k)
            continue

        if args.noop:
            sys.stdout.write("### Write {0}\n{1}".format(k, files_to_write[k]))
            continue

        retries = 0
        while True:
            try:
                log.debug("Writing output file : %s" % k)
                with safe_open(k, 'w') as outfile:
                    outfile.write(files_to_write[k])
                log.debug(" ... done")
                break
            except IOError as e:
                # if we got ELOOP the file was a dangling or bad
                # symlink.  We're taking ownership of this, so
                # overwrite it.
                if e.errno == errno.ELOOP and retries < 1:
                    log.debug("Dangling symlink <%s>; "
                              "unlinking and trying again" % k)
                    os.unlink(k)
                    retries = 1
                    continue
                elif e.errno == errno.EACCESS:
                    log.debug(" ... is read only, skipped")
                    break
                else:
                    raise


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
    log.debug("Checking if interface %s has an active link carrier." % iface)
    if is_interface_live(iface, sys_root):
        return True

    if args.noop:
        return False

    subprocess.check_call(['ip', 'link', 'set', 'dev', iface, 'up'])

    return True


def is_interface_vlan(iface, distro):
    if distro in ('debian', 'ubuntu'):
        file_name = '/etc/network/interfaces.d/%s.cfg' % iface
        if os.path.exists(file_name):
            return 'vlan-raw-device' in open(file_name).read()
    elif distro in ('redhat', 'centos', 'fedora', 'suse', 'opensuse'):
        file_name = '/etc/sysconfig/network-scripts/ifcfg-%s' % iface
        if os.path.exists(file_name):
            return 'VLAN=YES' in open(file_name).read()
    elif distro in ('gentoo'):
        file_name = '/etc/conf.d/net.%s' % iface
        if os.path.exists(file_name):
            return 'vlan_id' in open(file_name).read()

    return False


def is_interface_bridge(iface, distro):
    if distro in ('debian', 'ubuntu'):
        file_name = '/etc/network/interfaces.d/%s.cfg' % iface
        if os.path.exists(file_name):
            return 'bridge_ports' in open(file_name).read().lower()
    elif distro in ('redhat', 'centos', 'fedora', 'suse', 'opensuse'):
        file_name = '/etc/sysconfig/network-scripts/ifcfg-%s' % iface
        if os.path.exists(file_name):
            return 'type=bridge' in open(file_name).read().lower()
    elif distro in ('gentoo'):
        file_name = '/etc/conf.d/net.%s' % iface
        if os.path.exists(file_name):
            return 'bridge' in open(file_name).read().lower()

    return False


def get_sys_interfaces(interface, args):
    log.debug("Probing system interfaces")
    sys_root = os.path.join(args.root, 'sys/class/net')

    ignored_interfaces = ('sit', 'tunl', 'bonding_master', 'teql',
                          'ip6_vti', 'ip6tnl', 'bond', 'lo')
    sys_interfaces = {}
    if interface is not None:
        log.debug("Only considering interface %s from arguments" % interface)
        interfaces = [interface]
    else:
        interfaces = [f for f in os.listdir(sys_root)
                      if not f.startswith(ignored_interfaces)]
    # build interface dict. so we can enumerate through later
    if_dict = {}
    for iface in interfaces:
        # if interface is for an already configured vlan, skip it
        if is_interface_vlan(iface, args.distro):
            log.debug("Skipping vlan %s" % iface)
            continue

        # if interface is for an already configured bridge, skip it
        if is_interface_bridge(iface, args.distro):
            log.debug("Skipping bridge %s" % iface)
            continue

        mac_addr_type = open(
            '%s/%s/addr_assign_type' % (sys_root, iface), 'r').read().strip()
        # Interfaces without a permanent address are likely created by some
        # other system on the host like a running neutron agent. In these cases
        # that system should be responsible for configuring the interface not
        # glean.
        if mac_addr_type != PERMANENT_ADDR_TYPE:
            continue
        # check if interface is up if not try and bring it up
        if interface_live(iface, sys_root, args):
            mac = open('%s/%s/address' % (sys_root, iface), 'r').read().strip()
            if_dict[iface] = mac

    # wait up to 9 seconds all interfaces to reach up
    log.debug("Waiting for interfaces to become active.")
    if_up_list = []
    for x in range(0, 90):
        for iface in if_dict:
            mac = if_dict[iface]
            if iface in if_up_list:
                continue
            if is_interface_live(iface, sys_root):
                # Add system interface
                sys_interfaces[mac] = iface
                log.debug("Added system interface %s (%s)" % (iface, mac))
                if_up_list.append(iface)

        if sorted(if_up_list) == sorted(if_dict.keys()):
            # all interfaces are up no need to continue looping
            break
        time.sleep(.1)

    if sorted(if_up_list) != sorted(if_dict.keys()):
        # not all interfaces became active with in the time limit
        for iface in if_dict:
            if iface in if_up_list:
                continue
            msg = "Skipping system interface %s (%s)" % (iface,
                                                         if_dict[iface])
            log.warn(msg)

    return sys_interfaces


def get_network_info(args):
    """Retrieves network info from config-drive.

    If there is no meta_data.json in config-drive, it means that there
    is no config drive mounted- which means we know nothing.
    """
    config_drive = os.path.join(args.root, 'mnt/config')
    network_info_file = '%s/openstack/latest/network_info.json' % config_drive
    network_data_file = '%s/openstack/latest/network_data.json' % config_drive
    vendor_data_file = '%s/openstack/latest/vendor_data.json' % config_drive

    network_info = {}
    if os.path.exists(network_info_file):
        log.debug("Found network_info file %s" % network_info_file)
        network_info = json.load(open(network_info_file))
    # network_data.json is the file written by nova that should be there.
    # Other cloud deployments may use the above network_info.json or
    # vendor_data.json but the canonical location is this one.
    if os.path.exists(network_data_file):
        log.debug("Found network_info file %s" % network_data_file)
        network_info = json.load(open(network_data_file))
    elif os.path.exists(vendor_data_file):
        log.debug("Found vendor_data_file file %s" % vendor_data_file)
        vendor_data = json.load(open(vendor_data_file))
        if 'network_info' in vendor_data:
            log.debug("Found network_info in vendor_data_file")
            network_info = vendor_data['network_info']
    else:
        log.debug("Did not find vendor_data or network_info in config-drive")

    if not network_info:
        log.debug("Found no network_info in config-drive!  "
                  "Asusming DHCP interfaces")

    return network_info


def write_network_info_from_config_drive(args):
    """Write network info from config-drive.

    If there is no meta_data.json in config-drive, it means that there
    is no config drive mounted- which means we know nothing.

    Returns False on any issue, which will cause the writing of
    DHCP network files.
    """

    network_info = get_network_info(args)

    dns = write_dns_info(get_dns_from_config_drive(network_info))
    interfaces = get_config_drive_interfaces(network_info)
    sys_interfaces = get_sys_interfaces(args.interface, args)

    write_static_network_info(interfaces, sys_interfaces, dns, args)


def write_ssh_keys(args):
    """Write ssh-keys from config-drive.

    If there is no meta_data.json in config-drive, it means that there
    is no config drive mounted- which means we do nothing.
    """

    config_drive = os.path.join(args.root, 'mnt/config')
    ssh_path = os.path.join(args.root, 'root/.ssh')
    meta_data_path = '%s/openstack/latest/meta_data.json' % config_drive
    if not os.path.exists(meta_data_path):
        return 0

    meta_data = json.load(open(meta_data_path))
    if 'public_keys' not in meta_data:
        return 0

    keys_to_write = []

    # if we have keys already there, we want to preserve them
    if os.path.exists('/root/.ssh/authorized_keys'):
        with open('/root/.ssh/authorized_keys', 'r') as fk:
            for line in fk:
                keys_to_write.append(line.strip())
    for (name, key) in meta_data['public_keys'].items():
        key_title = "# Injected key {name} by keypair extension".format(
            name=name)
        if key_title not in keys_to_write:
            keys_to_write.append(key_title)

        if key not in keys_to_write:
            keys_to_write.append(key)

    files_to_write = {
        '/root/.ssh/authorized_keys': '\n'.join(keys_to_write) + '\n',
    }
    try:
        os.mkdir(ssh_path, 0o700)
    except OSError as e:
        if e.errno != 17:  # not File Exists
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

    hostname = meta_data['name']
    log.debug("Got hostname from meta_data.json : %s" % hostname)
    # underscore is not a valid hostname, but it's easy to name your
    # host with that on the command-line.  be helpful...
    if '_' in hostname:
        hostname = hostname.replace('_', '-')
        log.debug("Fixed up hostname to %s" % hostname)

    ret = subprocess.call(['hostname', hostname])
    if ret != 0:
        raise RuntimeError('Error setting hostname')
    else:
        # gentoo's hostname file is in a different location
        if args.distro is 'gentoo':
            with open('/etc/conf.d/hostname', 'w') as fh:
                fh.write("hostname=\"{host}\"\n".format(host=hostname))
        else:
            with safe_open('/etc/hostname', 'w') as fh:
                fh.write(hostname)
                fh.write('\n')

        # generate the lists of hosts and ips
        hosts_to_add = {'localhost': '127.0.0.1'}

        # get information on the network
        hostname_ip = '127.0.1.1'
        network_info = get_network_info(args)
        if network_info:
            interfaces = get_config_drive_interfaces(network_info)
            keys = sorted(interfaces.keys())

            for key in keys:
                interface = interfaces[key]
                if interface and 'ip_address' in interface:
                    hostname_ip = interface['ip_address']
                    break

        # check short hostname and generate list for hosts
        hosts_to_add[hostname] = hostname_ip
        short_hostname = hostname.split('.')[0]
        if short_hostname != hostname:
            hosts_to_add[short_hostname] = hostname_ip

        for host in hosts_to_add:
            host_value = hosts_to_add[host]
            # See if we already have a hosts entry for hostname
            prog = re.compile('^%s .*%s\n' % (host_value, host))
            match = None
            if os.path.isfile('/etc/hosts'):
                with open('/etc/hosts') as fh:
                    match = prog.match(fh.read())

            # Write out a hosts entry for hostname
            if match is None:
                with safe_open('/etc/hosts', 'a+') as fh:
                    fh.write(u'%s %s\n' % (host_value, host))


def main():
    parser = argparse.ArgumentParser(description="Static network config")
    parser.add_argument(
        '-n', '--noop', action='store_true', help='Do not write files')
    parser.add_argument(
        '--distro', dest='distro',
        default=platform.dist()[0].lower(),
        help='Override distro (detected "%s")' % platform.dist()[0].lower())
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
    parser.add_argument(
        '--debug', dest='debug', action='store_true',
        help="Enable debugging output")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    log.debug("Starting glean")
    log.debug("Detected distro : %s" % args.distro)

    with systemlock.Lock('/tmp/glean.lock'):
        if args.ssh:
            write_ssh_keys(args)
        if args.hostname:
            set_hostname_from_config_drive(args)
        if args.interface != 'lo' and not args.skip:
            write_network_info_from_config_drive(args)
    log.debug("Done!")
    return 0


if __name__ == '__main__':
    sys.exit(main())
