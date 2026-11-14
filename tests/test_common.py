"""Logic tests without OPNsense: pfctl is replaced by a script that records
its calls.   python3 tests/test_common.py"""
import json, os, sys, tempfile, time, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'src/opnsense/scripts/deviceonboarding'))
import common

tmp = tempfile.mkdtemp()
calls = os.path.join(tmp, 'pfctl.log')
fake = os.path.join(tmp, 'pfctl')
open(fake, 'w').write(f'#!/bin/sh\necho "$@" >> {calls}\ncat >/dev/null 2>&1 || true\n')
os.chmod(fake, 0o755)
common.PFCTL = fake
common.STATE_DIR = tmp; common.STATE_FILE = tmp + '/state.json'; common.STATE_LOCK = tmp + '/state.lock'
common.CONF_FILE = tmp + '/conf.json'
common.log = lambda m, p=None: print('  log:', m)
leases = tmp + '/leases'
now = int(time.time())
open(leases, 'w').write(
    f'{now+3600} aa:bb:cc:00:00:01 192.168.50.150 new1 01:aa\n'
    f'{now+3600} aa:bb:cc:00:00:02 192.168.50.20 reserved *\n'   # outside pool
    f'{now-10} aa:bb:cc:00:00:03 192.168.50.151 expired *\n'     # expired
    'duid 00:01:00:01\n'
    f'{now+3600} 1234 2a11::5 v6host 00:01\n')
json.dump({'interface': 'bridge0', 'pool_start': '192.168.50.100', 'pool_end': '192.168.50.199',
           'duration': 10, 'internal_networks': '10.0.0.0/8,192.168.0.0/16', 'kill_states': True, 'log': True},
          open(common.CONF_FILE, 'w'))
conf = common.load_conf()

L = common.read_leases(leases)
assert [l['ip'] for l in L] == ['192.168.50.150', '192.168.50.20'], L
assert common.in_pool('192.168.50.150', conf) and not common.in_pool('192.168.50.20', conf)
rules = common.anchor_rules(conf); print(rules)
assert 'pass in log quick on bridge0 inet from <onboarding> to ! <onboarding_internal>' in rules
assert '{ 10.0.0.0/8, 192.168.0.0/16 }' in rules

with common.locked_state() as st:
    common.open_window(st, '192.168.50.150', 'aa:bb:cc:00:00:01', 'new1', 10)
st = json.load(open(common.STATE_FILE))
assert '192.168.50.150' in st['open'] and st['seen']['aa:bb:cc:00:00:01']['opened']
assert 9*60 < st['open']['192.168.50.150']['until'] - now <= 10*60 + 1
with common.locked_state() as st:
    st['open']['192.168.50.150']['until'] = now - 1
    for ip, e in list(st['open'].items()):
        if e['until'] <= now: common.close_window(st, ip, True)
assert json.load(open(common.STATE_FILE))['open'] == {}
log = open(calls).read(); print(log)
assert '-a deviceonboarding -t onboarding -T add 192.168.50.150' in log
assert '-a deviceonboarding -t onboarding -T delete 192.168.50.150' in log and '-k 192.168.50.150' in log

# ctl.py as a subprocess with the same paths
env = dict(os.environ)
ctl = os.path.join(HERE, '..', 'src/opnsense/scripts/deviceonboarding/ctl.py')
code = f"""
import sys; sys.argv=sys.argv
sys.path.insert(0, {os.path.dirname(ctl)!r}); import common
common.PFCTL={fake!r}; common.STATE_DIR={tmp!r}; common.STATE_FILE={tmp+'/state.json'!r}; common.STATE_LOCK={tmp+'/state.lock'!r}
common.CONF_FILE={common.CONF_FILE!r}; common.LEASE_FILE={leases!r}; common.log=lambda m,p=None: None
import runpy; runpy.run_path({ctl!r}, run_name='__main__')
"""
def run(*a):
    r = subprocess.run([sys.executable, '-c', code] + list(a), capture_output=True, text=True)
    return json.loads(r.stdout)
r = run('open', '192.168.50.150', '5'); print(' ctl open:', r); assert r['status'] == 'ok'
r = run('list'); print(' ctl list:', r['open']); assert r['open'][0]['mac'] == 'aa:bb:cc:00:00:01' and r['open'][0]['remaining'] <= 300
r = run('open', 'kaputt'); assert r['status'] == 'failed'
r = run('forget', 'aa:bb:cc:00:00:01'); assert r['status'] == 'ok'
r = run('list'); assert r['open'] == [] and r['seen'] == []
r = run('forget', 'nicht-mac'); assert r['status'] == 'failed'
# Regression: 'list' must not create state, baseline depends on the 'initialized' flag
os.remove(common.STATE_FILE)
run('list'); assert not os.path.exists(common.STATE_FILE), 'list created state.json'
with common.locked_state() as st:
    n = common.baseline(st, common.read_leases(leases), conf)
assert n == 1 and json.load(open(common.STATE_FILE))['seen']['aa:bb:cc:00:00:01']['baseline']
assert json.load(open(common.STATE_FILE))['open'] == {}
with common.locked_state() as st:
    assert common.baseline(st, common.read_leases(leases), conf) == 0   # only once
# Regression: after 'forget' a stale lease must not open a window,
# only a renewed lease (higher expiry) does
with common.locked_state() as st:
    st['seen'].pop('aa:bb:cc:00:00:01', None)
    st['forgotten']['aa:bb:cc:00:00:01'] = now + 3600
old_lease = {'mac': 'aa:bb:cc:00:00:01', 'ip': '192.168.50.150', 'host': 'new1', 'expiry': now + 3600}
new_lease = dict(old_lease, expiry=now + 7200)
with common.locked_state() as st:
    assert not common.is_new_contact(st, old_lease), 'stale lease must not open a window'
    assert common.is_new_contact(st, new_lease), 'renewed lease must open a window'
    assert 'aa:bb:cc:00:00:01' not in st['forgotten']
# IPv6: NDP parsing, rules with route-to, sync and close
ndp_out = '''Neighbor                             Linklayer Address  Netif Expire    S Flags
fe80::1266:6aff:fe62:4bd6%bridge0    aa:bb:cc:00:00:01 bridge0 23h45m50s S
2001:db8:50::1a2b                    aa:bb:cc:00:00:01 bridge0 23h45m50s S
2001:db8:50::77                      AA:BB:CC:00:00:01 bridge0 10s       R
2001:db8:50::1                       58:9c:fc:10:96:5c bridge0 permanent R
2001:db8:99::5                       aa:bb:cc:00:00:01 bridge1 10s       R
2001:db8:50::dead                    (incomplete)      bridge0 expired   N
'''
n = common.read_ndp('bridge0', ndp_out)
assert n['aa:bb:cc:00:00:01'] == ['2001:db8:50::1a2b', '2001:db8:50::77'], n
assert '(incomplete)' not in n
conf6 = dict(conf, ipv6_enabled=True, internal_networks6=['fc00::/7', 'fe80::/10'],
             gateway6_interface='wg0', gateway6_address='2001:db8:ffff::1')
r6 = common.anchor_rules(conf6); print(r6)
assert 'pass in log quick on bridge0 route-to (wg0 2001:db8:ffff::1) inet6 from <onboarding6> to ! <onboarding6_internal>' in r6
assert 'inet6' not in common.anchor_rules(conf), 'IPv6 rule must only exist when enabled'
assert 'route-to' not in common.anchor_rules(dict(conf6, gateway6_interface='', gateway6_address=''))
open(calls, 'w').close()
with common.locked_state() as st:
    st['open'] = {}
    common.open_window(st, '192.168.50.150', 'aa:bb:cc:00:00:01', 'new1', 10)
    common.sync_ipv6(st, conf6, ndp=n)
    assert st['open']['192.168.50.150']['ip6'] == ['2001:db8:50::1a2b', '2001:db8:50::77']
    common.sync_ipv6(st, conf6, ndp=n)    # nothing new -> no second add
    common.close_window(st, '192.168.50.150', True)
log6 = open(calls).read(); print(log6)
assert log6.count('-t onboarding6 -T add') == 1
assert '-t onboarding6 -T delete 2001:db8:50::1a2b 2001:db8:50::77' in log6
assert '-k 2001:db8:50::77' in log6
print('ALL TESTS PASSED')
