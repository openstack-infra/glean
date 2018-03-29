# Copyright (c) Red Hat, Inc.
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


# http://stackoverflow.com/questions/33531561/python-ipaddr-library-netmask-ipv6
def ipv6_netmask_length(netmask):
    bitCount = [
        0, 0x8000, 0xc000, 0xe000, 0xf000, 0xf800, 0xfc00, 0xfe00, 0xff00,
        0xff80, 0xffc0, 0xffe0, 0xfff0, 0xfff8, 0xfffc, 0xfffe, 0xffff]

    count = 0
    try:
        for w in netmask.split(':'):
            if not w or int(w, 16) == 0:
                break
            count += bitCount.index(int(w, 16))
    except:
        raise SyntaxError('Bad Netmask')
    return count


# code to convert netmask ip to cidr number
# https://stackoverflow.com/a/43885814
def ipv4_netmask_length(netmask):
    '''
    :param netmask: netmask ip addr (eg: 255.255.255.0)
    :return: equivalent cidr number to given netmask ip (eg: 24)
    '''
    return sum([bin(int(x)).count('1') for x in netmask.split('.')])
