"""
Device Onboarding – shared helpers for the watcher (watch.py) and the control
script used by configd / the GUI (ctl.py).

State lives in /var/db/deviceonboarding/state.json:
    seen: {mac: {first, last, ip, host, opened, baseline}}
    open: {ip:  {mac, host, since, until, ip6: [...]}}   (ip = IPv4 key)
All modifications happen under an exclusive flock on STATE_LOCK.
"""

import fcntl
import ipaddress
import json
import os
import subprocess
import syslog
import time
from contextlib import contextmanager

CONF_FILE = '/usr/local/etc/deviceonboarding/deviceonboarding.json'
STATE_DIR = '/var/db/deviceonboarding'
STATE_FILE = STATE_DIR + '/state.json'
STATE_LOCK = STATE_DIR + '/state.lock'
LEASE_FILE = '/var/db/dnsmasq.leases'
PFCTL = '/sbin/pfctl'
NDP = '/usr/sbin/ndp'

ANCHOR = 'deviceonboarding'
TABLE = 'onboarding'
TABLE_INTERNAL = 'onboarding_internal'
TABLE6 = 'onboarding6'
TABLE6_INTERNAL = 'onboarding6_internal'


def log(msg, prio=syslog.LOG_NOTICE):
    syslog.openlog('deviceonboarding', syslog.LOG_PID, syslog.LOG_DAEMON)
    syslog.syslog(prio, msg)


def load_conf():
    """Configuration written by the configd template (see +TARGETS)."""
    with open(CONF_FILE) as f:
        conf = json.load(f)
    conf['duration'] = int(conf.get('duration') or 10)
    conf['internal_networks'] = [
        str(ipaddress.ip_network(n.strip(), strict=False))
        for n in str(conf.get('internal_networks', '')).split(',') if n.strip()
    ]
    conf['internal_networks6'] = [
        str(ipaddress.ip_network(n.strip(), strict=False))
        for n in str(conf.get('internal_networks6', '')).split(',') if n.strip()
    ]
    conf['ipv6_enabled'] = bool(conf.get('ipv6_enabled'))
    return conf


def read_ndp(interface, ndp_output=None):
    """Global IPv6 addresses per MAC on an interface, from the NDP table.

    'ndp -an' columns: Neighbor  Linklayer-Address  Netif  Expire  S  Flags
    Link-local addresses and incomplete entries are skipped.
    """
    if ndp_output is None:
        r = subprocess.run([NDP, '-an'], stdin=subprocess.DEVNULL, capture_output=True, text=True)
        ndp_output = r.stdout
    result = {}
    for line in ndp_output.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 3 or parts[2] != interface:
            continue
        addr = parts[0].split('%')[0]
        mac = parts[1].lower()
        if mac.count(':') != 5:
            continue
        try:
            ip = ipaddress.IPv6Address(addr)
        except ValueError:
            continue
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            continue
        result.setdefault(mac, []).append(str(ip))
    return result


def in_pool(ip, conf):
    try:
        a = int(ipaddress.IPv4Address(ip))
        return int(ipaddress.IPv4Address(conf['pool_start'])) <= a <= int(ipaddress.IPv4Address(conf['pool_end']))
    except (ipaddress.AddressValueError, KeyError, ValueError):
        return False


def read_leases(path=None):
    """Active IPv4 leases from the dnsmasq lease file.

    Format per line: <expiry> <mac> <ip> <hostname> <client-id>
    IPv6 leases follow a 'duid' line and are ignored.
    """
    path = path or LEASE_FILE
    now = time.time()
    leases = []
    try:
        with open(path) as f:
            for line in f:
                parts = line.split()
                if len(parts) < 4 or parts[0] == 'duid':
                    continue
                try:
                    expiry = int(parts[0])
                    ip = str(ipaddress.IPv4Address(parts[2]))
                except ValueError:
                    continue
                if expiry != 0 and expiry < now:
                    continue
                host = parts[3] if parts[3] != '*' else ''
                leases.append({'mac': parts[1].lower(), 'ip': ip, 'host': host, 'expiry': expiry})
    except FileNotFoundError:
        pass
    return leases


@contextmanager
def locked_state(readonly=False):
    """Load the state under an exclusive lock and save it on exit
    (unless readonly – reading must never create or change the state)."""
    os.makedirs(STATE_DIR, mode=0o750, exist_ok=True)
    with open(STATE_LOCK, 'a') as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
        except (FileNotFoundError, ValueError):
            state = {}
        state.setdefault('seen', {})
        state.setdefault('open', {})
        state.setdefault('forgotten', {})
        yield state
        if readonly:
            return
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(state, f, indent=1, sort_keys=True)
        os.replace(tmp, STATE_FILE)


def is_new_contact(state, lease):
    """A MAC that is not known yet. After 'forget', the lease that existed at
    that moment does not count: the device has to renew/request a lease
    (higher expiry) to get a new window."""
    mac = lease['mac']
    if mac in state['seen']:
        return False
    tomb = state['forgotten'].get(mac)
    if tomb is not None and lease.get('expiry', 0) <= tomb:
        return False
    state['forgotten'].pop(mac, None)
    return True


def baseline(state, leases, conf):
    """First run: record all current leases in the pool as known, without
    opening anything. Marked by state['initialized'], not by the file."""
    if state.get('initialized'):
        return 0
    now = int(time.time())
    count = 0
    for lease in leases:
        if in_pool(lease['ip'], conf) and lease['mac'] not in state['seen']:
            state['seen'][lease['mac']] = {'first': now, 'last': now, 'ip': lease['ip'],
                                           'host': lease['host'], 'opened': False, 'baseline': True}
            count += 1
    state['initialized'] = now
    return count


def _pfctl(args, stdin=None):
    if stdin is None:
        return subprocess.run([PFCTL] + args, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return subprocess.run([PFCTL] + args, input=stdin, capture_output=True, text=True)


def anchor_rules(conf):
    internal = ', '.join(conf['internal_networks'])
    logkw = 'log ' if conf.get('log') else ''
    rules = (
        f'table <{TABLE}> persist\n'
        f'table <{TABLE_INTERNAL}> const {{ {internal} }}\n'
        f'pass in {logkw}quick on {conf["interface"]} inet '
        f'from <{TABLE}> to ! <{TABLE_INTERNAL}> keep state label "deviceonboarding"\n'
    )
    if conf.get('ipv6_enabled'):
        internal6 = ', '.join(conf['internal_networks6']) or 'fe80::/10'
        route = ''
        if conf.get('gateway6_interface') and conf.get('gateway6_address'):
            route = f'route-to ({conf["gateway6_interface"]} {conf["gateway6_address"]}) '
        rules += (
            f'table <{TABLE6}> persist\n'
            f'table <{TABLE6_INTERNAL}> const {{ {internal6} }}\n'
            f'pass in {logkw}quick on {conf["interface"]} {route}inet6 '
            f'from <{TABLE6}> to ! <{TABLE6_INTERNAL}> keep state label "deviceonboarding6"\n'
        )
    return rules


def anchor_loaded():
    r = _pfctl(['-a', ANCHOR, '-s', 'rules'])
    return r.returncode == 0 and 'deviceonboarding' in r.stdout


def load_anchor(conf, open_entries):
    """(Re)load the anchor rules and restore the currently open addresses.

    Loading the anchor resets the tables, so open entries are added again.
    open_entries: the state['open'] dict (IPv4 -> entry with optional ip6 list).
    """
    r = _pfctl(['-a', ANCHOR, '-f', '-'], stdin=anchor_rules(conf))
    if r.returncode != 0:
        log(f'failed to load anchor: {r.stderr.strip()}', syslog.LOG_ERR)
        return False
    ips = list(open_entries.keys())
    if ips:
        _pfctl(['-a', ANCHOR, '-t', TABLE, '-T', 'add'] + ips)
    ip6 = [a for e in open_entries.values() for a in e.get('ip6', [])]
    if ip6 and conf.get('ipv6_enabled'):
        _pfctl(['-a', ANCHOR, '-t', TABLE6, '-T', 'add'] + ip6)
    return True


def flush_anchor():
    _pfctl(['-a', ANCHOR, '-F', 'all'])


def table_add(ip):
    return _pfctl(['-a', ANCHOR, '-t', TABLE, '-T', 'add', ip]).returncode == 0


def table_delete(ip, kill_states=True):
    _pfctl(['-a', ANCHOR, '-t', TABLE, '-T', 'delete', ip])
    if kill_states:
        _pfctl(['-k', ip])


def table6_add(addrs):
    if addrs:
        _pfctl(['-a', ANCHOR, '-t', TABLE6, '-T', 'add'] + list(addrs))


def table6_delete(addrs, kill_states=True):
    if not addrs:
        return
    _pfctl(['-a', ANCHOR, '-t', TABLE6, '-T', 'delete'] + list(addrs))
    if kill_states:
        for a in addrs:
            _pfctl(['-k', a])


def sync_ipv6(state, conf, ndp=None):
    """Add the global IPv6 addresses of every device with an open window.
    Addresses are only added while the window is open; they are removed
    together with the window."""
    if not conf.get('ipv6_enabled') or not state['open']:
        return
    if ndp is None:
        ndp = read_ndp(conf['interface'])
    for ip, entry in state['open'].items():
        known = set(entry.get('ip6', []))
        new = [a for a in ndp.get(entry.get('mac', ''), []) if a not in known]
        if new:
            table6_add(new)
            entry['ip6'] = sorted(known | set(new))
            log(f'window for {ip} ({entry.get("mac")}): IPv6 {", ".join(new)}')


def open_window(state, ip, mac, host, minutes):
    now = int(time.time())
    entry = state['open'].get(ip)
    until = now + minutes * 60
    if entry and entry.get('mac') == mac:
        entry['until'] = max(entry['until'], until)
    else:
        state['open'][ip] = {'mac': mac, 'host': host, 'since': now, 'until': until}
    table_add(ip)
    seen = state['seen'].setdefault(mac, {'first': now})
    seen.update({'last': now, 'ip': ip, 'host': host or seen.get('host', ''), 'opened': True})
    log(f'window opened for {ip} ({mac} {host}) until {time.strftime("%H:%M:%S", time.localtime(state["open"][ip]["until"]))}')


def close_window(state, ip, kill_states=True, reason='expired'):
    entry = state['open'].pop(ip, None)
    table_delete(ip, kill_states)
    if entry:
        table6_delete(entry.get('ip6', []), kill_states)
    if entry:
        log(f'window closed for {ip} ({entry.get("mac")}): {reason}')
    return entry is not None
