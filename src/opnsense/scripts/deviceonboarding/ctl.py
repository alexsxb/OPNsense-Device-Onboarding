#!/usr/local/bin/python3
"""
Device Onboarding control script, called by configd (see actions_deviceonboarding.conf).

    ctl.py list
    ctl.py open <ip> [minutes]     minutes 0/missing = configured duration
    ctl.py close <ip>
    ctl.py forget <mac>

Always prints a JSON object.
"""

import ipaddress
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def out(obj):
    print(json.dumps(obj))
    sys.exit(0)


def valid_ip(s):
    try:
        return str(ipaddress.IPv4Address(s))
    except ValueError:
        out({'status': 'failed', 'message': 'invalid IPv4 address'})


def main(argv):
    cmd = argv[1] if len(argv) > 1 else 'list'
    try:
        conf = common.load_conf()
    except (OSError, ValueError):
        conf = {'duration': 10, 'kill_states': True}

    if cmd == 'list':
        now = int(time.time())
        with common.locked_state(readonly=True) as state:
            opened = [
                {'ip': ip, 'mac': e.get('mac'), 'host': e.get('host', ''), 'since': e.get('since'),
                 'until': e.get('until'), 'remaining': max(0, e.get('until', 0) - now), 'ip6': e.get('ip6', [])}
                for ip, e in sorted(state['open'].items())
            ]
            seen = [dict(v, mac=k) for k, v in sorted(state['seen'].items(), key=lambda kv: -kv[1].get('first', 0))]
        out({'status': 'ok', 'enabled': bool(conf.get('enabled')), 'open': opened, 'seen': seen,
             'duration': conf.get('duration')})

    if cmd == 'open':
        ip = valid_ip(argv[2] if len(argv) > 2 else '')
        try:
            minutes = int(argv[3]) if len(argv) > 3 else 0
        except ValueError:
            minutes = 0
        if not 1 <= minutes <= 1440:
            minutes = conf.get('duration', 10)
        lease = next((l for l in common.read_leases() if l['ip'] == ip), None)
        with common.locked_state() as state:
            entry = state['open'].get(ip, {})
            mac = (lease or {}).get('mac') or entry.get('mac') or 'manual'
            host = (lease or {}).get('host') or entry.get('host', '')
            common.open_window(state, ip, mac, host, minutes)
            if conf.get('interface'):
                common.sync_ipv6(state, conf)
            until = state['open'][ip]['until']
        out({'status': 'ok', 'ip': ip, 'until': until})

    if cmd == 'close':
        ip = valid_ip(argv[2] if len(argv) > 2 else '')
        with common.locked_state() as state:
            found = common.close_window(state, ip, conf.get('kill_states', True), reason='closed manually')
        out({'status': 'ok' if found else 'not_found', 'ip': ip})

    if cmd == 'forget':
        mac = (argv[2] if len(argv) > 2 else '').lower()
        if not re.fullmatch(r'([0-9a-f]{2}:){5}[0-9a-f]{2}', mac):
            out({'status': 'failed', 'message': 'invalid MAC address'})
        expiry = max([l['expiry'] for l in common.read_leases() if l['mac'] == mac] or [0])
        with common.locked_state() as state:
            removed = state['seen'].pop(mac, None) is not None
            state['forgotten'][mac] = expiry
            for ip, e in list(state['open'].items()):
                if e.get('mac') == mac:
                    common.close_window(state, ip, conf.get('kill_states', True), reason='forgotten')
        if removed:
            common.log(f'{mac} forgotten, it gets a new window when it requests a new lease')
        out({'status': 'ok' if removed else 'not_found', 'mac': mac})

    out({'status': 'failed', 'message': f'unknown command {cmd}'})


if __name__ == '__main__':
    main(sys.argv)
