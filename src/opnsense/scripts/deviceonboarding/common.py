"""
Device Onboarding – shared helpers for the watcher (watch.py) and the control
script used by configd / the GUI (ctl.py).

State lives in /var/db/deviceonboarding/state.json:
    seen: {mac: {first, last, ip, host, opened, baseline}}
    open: {ip:  {mac, host, since, until}}
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

ANCHOR = 'deviceonboarding'
TABLE = 'onboarding'
TABLE_INTERNAL = 'onboarding_internal'


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
    return conf


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
    return (
        f'table <{TABLE}> persist\n'
        f'table <{TABLE_INTERNAL}> const {{ {internal} }}\n'
        f'pass in {logkw}quick on {conf["interface"]} inet '
        f'from <{TABLE}> to ! <{TABLE_INTERNAL}> keep state label "deviceonboarding"\n'
    )


def anchor_loaded():
    r = _pfctl(['-a', ANCHOR, '-s', 'rules'])
    return r.returncode == 0 and 'deviceonboarding' in r.stdout


def load_anchor(conf, open_ips):
    """(Re)load the anchor rules and restore the currently open addresses.

    Loading the anchor resets the table, so open entries are added again.
    """
    r = _pfctl(['-a', ANCHOR, '-f', '-'], stdin=anchor_rules(conf))
    if r.returncode != 0:
        log(f'failed to load anchor: {r.stderr.strip()}', syslog.LOG_ERR)
        return False
    if open_ips:
        _pfctl(['-a', ANCHOR, '-t', TABLE, '-T', 'add'] + list(open_ips))
    return True


def flush_anchor():
    _pfctl(['-a', ANCHOR, '-F', 'all'])


def table_add(ip):
    return _pfctl(['-a', ANCHOR, '-t', TABLE, '-T', 'add', ip]).returncode == 0


def table_delete(ip, kill_states=True):
    _pfctl(['-a', ANCHOR, '-t', TABLE, '-T', 'delete', ip])
    if kill_states:
        _pfctl(['-k', ip])


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
        log(f'window closed for {ip} ({entry.get("mac")}): {reason}')
    return entry is not None
