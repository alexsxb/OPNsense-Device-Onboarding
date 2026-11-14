#!/usr/local/bin/python3
"""
Device Onboarding watcher.

Every INTERVAL seconds:
  * make sure the pf anchor is loaded (a firewall reload may have emptied it),
  * look at the dnsmasq leases inside the configured pool,
  * open a window for every MAC address never seen before,
  * close windows whose time is up.

On the very first start (no state file yet) all current leases are only
recorded as known ("baseline"), so installing the plugin does not open
the internet for every device that already exists.
"""

import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

INTERVAL = 15
running = True


def stop(signum, frame):
    global running
    running = False


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    conf = common.load_conf()
    conf_mtime = os.path.getmtime(common.CONF_FILE)
    with common.locked_state() as state:
        if not state.get('initialized'):
            n = common.baseline(state, common.read_leases(), conf)
            common.log(f'baseline: {n} existing devices recorded as known')
        common.load_anchor(conf, state['open'])
    common.log(f'started: interface {conf["interface"]}, pool {conf["pool_start"]}-{conf["pool_end"]}, '
              f'{conf["duration"]} min')

    while running:
        try:
            mtime = os.path.getmtime(common.CONF_FILE)
            reload_anchor = False
            if mtime != conf_mtime:
                conf, conf_mtime, reload_anchor = common.load_conf(), mtime, True
                common.log('configuration reloaded')

            with common.locked_state() as state:
                if reload_anchor or not common.anchor_loaded():
                    common.load_anchor(conf, state['open'])

                now = int(time.time())
                for lease in common.read_leases():
                    if not common.in_pool(lease['ip'], conf):
                        continue
                    mac, ip, host = lease['mac'], lease['ip'], lease['host']
                    if common.is_new_contact(state, lease):
                        state['seen'][mac] = {'first': now, 'last': now, 'ip': ip, 'host': host, 'opened': False}
                        common.open_window(state, ip, mac, host, conf['duration'])
                    elif mac in state['seen']:
                        state['seen'][mac].update({'last': now, 'ip': ip})
                        if host:
                            state['seen'][mac]['host'] = host

                common.sync_ipv6(state, conf)

                for ip, entry in list(state['open'].items()):
                    if entry['until'] <= now:
                        common.close_window(state, ip, conf.get('kill_states', True))
        except Exception as exc:  # keep running, but leave a trace
            common.log(f'error: {exc!r}')
        for _ in range(INTERVAL):
            if not running:
                break
            time.sleep(1)

    # stopping the service closes all windows
    with common.locked_state() as state:
        for ip in list(state['open'].keys()):
            common.close_window(state, ip, conf.get('kill_states', True), reason='service stopped')
    common.flush_anchor()
    common.log('stopped')


if __name__ == '__main__':
    main()
