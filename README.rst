=====
glean
=====

Glean is a program intended to configure a system based on
configuration provided in a `configuration drive
<http://docs.openstack.org/user-guide/cli_config_drive.html>`__.

Why would you want glean?
-------------------------

Different cloud providers have different ways of providing networking
and other configuration to guest virtual-machines.  Many use DHCP but
others, notably Rackspace, use configuration provided via a
configuration drive.

What does glean do?
-------------------

Broadly, glean checks for configuration drive based information and,
if found, uses that to configure the network.  If config-drive is not
found, it falls back to configuring any available interfaces with
DHCP.

Specifically, it will mount the special block-device with label
`config-2` and parse the `network_info.json` and `vendor_info.json`
files within.  If appropriate network configuration is found, it will
write out network configuration files.

The format of the `network_info.json` file is documented `here
<http://specs.openstack.org/openstack/nova-specs/specs/liberty/implemented/metadata-service-network-info.html#rest-api-impact>`__.
Please note that glean does not implement every feature listed.

If no network info is found there, available interfaces can be probed
from `/sys/class/net` and any that appear to be up will be configured
for use with DHCP.

It will also handle `authorized_keys` and host-name info provided from
`meta_data.json`.

How does glean do this?
+++++++++++++++++++++++

systemd environment
===================

glean install will add a `udev` rules file (`99-glean.rules`) that
triggers on any network device being added.  This will run the
`glean@.service` systemd template for the interface specified.

This systemd unit firstly determines if there is already a
`/etc/sysconfig/network/` configuration for the interface; if so, the
interface is considered configured and skipped.

If not, glean is started with the interface specified.  The
configuration drive is probed to see if network configuration for the
interface is available.  If so, it will be added, otherwise the
interface will configured for DHCP.

How do I use glean?
-------------------

Glean ships `glean-install`, a script which install glean into your
system startup environment.  It should handle `sysv`, `upstart` and
`systemd` to cover all major distributions.  This should be run once,
during install or image build.

The startup environment will be modified as described above to
configure any found interfaces.

Differences to cloud-init?
--------------------------

Glean differs to `cloud-init` mainly in its very reduced dependency
footprint.  In a dynamic CI environment such as OpenStack, many of the
python dependencies for `cloud-init` can cause conflicts with packages
or versions required for testing.

Glean also better supports static IP allocation within config-drive,
particuarly important within the Rackspace environment.

More details
------------

* Free software: Apache license
* Documentation: http://docs.openstack.org/infra/glean
* Source: http://git.openstack.org/cgit/openstack-infra/glean
* Bugs: http://storyboard.openstack.org
