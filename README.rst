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

Glean firstly checks for configuration drive based information and, if
found, uses that for network configuration.  If config-drive is not
found, it falls back to configuring any available interfaces with DHCP.

Specifically, it will mount the special block-device with label
`config-2` and parse the `network_info.json` and `vendor_info.json`
files within.  If appropriate network configuration is found, it will
write out network configuration files (e.g. `/etc/sysconfig/network/`
scripts on Red Hat platforms, `/etc/interfaces` on Debian, etc).

If no network info is found there, available interfaces will be probed
from `/sys/class/net` and any that appear to be up will be configured
for use with DHCP.

It will also handle `authorized_keys` and host-name info provided from
`meta_data.json`.

How do I use glean?
-------------------

Glean ships `glean-install`, a script which install glean into your
system startup environment.  It should handle `sysv`, `upstart` and
`systemd` to cover all major distributions.  This should be run once,
during install or image build.

The startup environment will run `glean.sh`, which configures any
found interfaces as described above.

Differences to cloud-config?
----------------------------

...

* Free software: Apache license
* Documentation: http://docs.openstack.org/infra/glean
* Source: http://git.openstack.org/cgit/openstack-infra/glean
* Bugs: http://storyboard.openstack.org
