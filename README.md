# os-device-onboarding

An OPNsense plugin for onboarding devices in a restricted network, typically an
IoT VLAN without internet access.

- **Setup window:** a device that appears for the **first time** in the dynamic
  DHCP pool of an interface gets internet access for a few minutes (default 10),
  so it can finish its setup with the vendor app or cloud. Afterwards your regular
  firewall policy applies again.
- **Permanent access:** with one click, a device gets a fixed DHCP reservation and
  is added to the firewall aliases that your own rules use for permanent internet
  access. The plugin keeps no list of its own; the aliases remain the single
  source of truth and keep working without the plugin.

It is meant for the usual IoT workflow: plug in a new smart plug, camera or
appliance, let it register for a few minutes, then decide whether it needs
internet permanently, and keep it isolated otherwise.

## Requirements

- OPNsense 25.x / 26.x (developed and tested on 26.7)
- **Dnsmasq** as DHCP server on the interface (the plugin reads
  `/var/db/dnsmasq.leases` and manages Dnsmasq host reservations)
- For permanent access management: an existing *Host(s)* alias and an existing
  *MAC address* alias, each referenced by a pass rule of yours (see below)

## How it works

```
new MAC in DHCP pool ──► watcher ──► pf anchor "deviceonboarding"
                                       table <onboarding>           (open windows)
                                       table <onboarding_internal>  (blocked destinations)
                                       pass in log quick on <if> inet
                                            from <onboarding> to ! <onboarding_internal>
```

- The service `deviceonboarding` (supervised by `daemon(8)`) reads the Dnsmasq
  leases every 15 seconds.
- A MAC address that has never been seen inside the configured pool opens a
  window: its IPv4 address is added to `<onboarding>` and removed again when the
  time is up. Open connections can be dropped at that moment (`pfctl -k`).
- Every MAC address gets **one** window. "Forget" in the GUI allows another one
  as soon as the device requests a new lease.
- **First start:** all devices already present in the pool are only recorded as
  known (baseline). Installing or enabling the plugin opens nothing.
- Destinations in *Excluded networks* (default: RFC 1918, CGNAT, link-local)
  stay blocked during the window, so new devices reach the internet but not
  your other networks.
- The plugin registers the anchor in the ruleset only while it is enabled.
- IPv4 only. Addresses outside the pool (your reservations) never get a window.

## Permanent access

Create two aliases and the matching rules yourself, for example on the IoT
interface:

| Alias | Type | Rule |
|---|---|---|
| `IOT_INTERNET` | Host(s) | pass, IPv4, source `IOT_INTERNET`, destination: not your internal networks |
| `IOT_INTERNET_MAC` | MAC address | pass, IPv6, source `IOT_INTERNET_MAC`, destination: not your internal networks |

Select both aliases and a reservation range (outside the dynamic pool) in the
plugin settings. Then:

- **Grant permanent access** (star button on an open window or a known device,
  or by MAC address): the plugin creates a Dnsmasq reservation with the next free
  address of the range (or reuses / moves an existing one), adds the IP to the
  host alias and the MAC to the MAC alias, applies Dnsmasq and the aliases and
  closes the setup window. The device gets its new address with its next lease.
- **Remove permanent access:** removes the device from both aliases and drops
  its connections. The reservation is kept.

Using a MAC alias for IPv6 is useful because devices form their IPv6 addresses
themselves (SLAAC). For IPv4 a host alias with the fixed reservation is used: a
MAC alias only knows a device after it appears in the ARP table, which can delay
the first packets of devices that are asleep most of the time.

## Installation

The plugin is not part of the official OPNsense repository and is built on the
firewall itself.

1. Once, on the OPNsense host, fetch the plugins source tree:
   ```sh
   sudo opnsense-code plugins
   ```
2. From a checkout of this repository:
   ```sh
   scripts/deploy.sh user@opnsense-host
   ```
   The script copies the sources to `/usr/plugins/net/device-onboarding`, runs
   `make package` and installs the package with `pkg add`.

Alternatively copy the repository to `/usr/plugins/net/device-onboarding` by
hand and run `make package` there.

Then open **Services → Device Onboarding**, select the interface and the DHCP
pool, enable the plugin and click *Apply*.

After a reinstallation of OPNsense the package has to be built again; the
settings are part of `config.xml` and come back with a configuration restore.

**Uninstall:** disable the plugin and apply, then `sudo pkg delete os-device-onboarding`.

## Settings

| Setting | Meaning |
|---|---|
| Enable | Start the watcher and register the firewall anchor |
| Interface | Interface the new devices are connected to |
| Pool start / end | Dynamic DHCP range. Only leases inside get a window |
| Duration | Window length in minutes (1–1440) |
| Excluded networks | Destinations that stay blocked during the window |
| Kill states on close | Drop open connections when a window closes |
| Log | Log traffic passed by the setup rule |
| IPv4 alias / MAC alias | Existing aliases for permanent access (empty = feature off) |
| Reservation range | Range for new fixed reservations, outside the pool |
| Reservation domain | Optional domain for new reservations |

## API

| Method | Path | |
|---|---|---|
| GET | `/api/deviceonboarding/settings/get` | settings |
| POST | `/api/deviceonboarding/settings/set` | save settings |
| POST | `/api/deviceonboarding/service/reconfigure` | apply (templates, service, firewall) |
| GET | `/api/deviceonboarding/service/list` | open windows and known devices |
| POST | `/api/deviceonboarding/service/open` | `ip`, `minutes` – open or extend a window |
| POST | `/api/deviceonboarding/service/close/<ip>` | close a window |
| POST | `/api/deviceonboarding/service/forget/<mac>` | forget a device |
| GET | `/api/deviceonboarding/permanent/list` | devices with permanent access |
| POST | `/api/deviceonboarding/permanent/add` | `mac`, optional `ip`, `host` |
| POST | `/api/deviceonboarding/permanent/remove` | `mac` |

## Troubleshooting

```sh
sudo pfctl -a deviceonboarding -s rules                  # rule loaded?
sudo pfctl -a deviceonboarding -t onboarding -T show     # open windows
sudo /usr/local/opnsense/scripts/deviceonboarding/ctl.py list
grep deviceonboarding /var/log/system/latest.log
```

Runtime state (known devices, open windows) is kept in
`/var/db/deviceonboarding/state.json`.

## Repository layout

| Path | Purpose |
|---|---|
| `src/opnsense/mvc/app/models/OPNsense/DeviceOnboarding/` | model (`OPNsense/deviceonboarding` in `config.xml`), ACL, menu |
| `src/opnsense/mvc/app/controllers/OPNsense/DeviceOnboarding/` | GUI and API controllers |
| `src/opnsense/mvc/app/views/OPNsense/DeviceOnboarding/index.volt` | GUI page |
| `src/opnsense/scripts/deviceonboarding/` | `watch.py` (service), `ctl.py` (configd/GUI), `common.py` |
| `src/opnsense/service/conf/actions.d/actions_deviceonboarding.conf` | configd actions |
| `src/opnsense/service/templates/OPNsense/DeviceOnboarding/` | rc.conf.d and service configuration |
| `src/etc/rc.d/deviceonboarding` | rc script |
| `src/etc/inc/plugins.inc.d/deviceonboarding.inc` | service registration, firewall anchor |
| `tests/test_common.py` | logic tests without OPNsense (`python3 tests/test_common.py`) |

## License

BSD 2-Clause, see [LICENSE](LICENSE).
