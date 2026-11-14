<?php

/*
 * Device Onboarding – permanent internet access.
 *
 * The plugin does not keep its own list. It maintains the existing firewall
 * aliases (IPv4 addresses and MAC addresses, see settings alias_ipv4 /
 * alias_mac) and a fixed Dnsmasq DHCP reservation per device, so the regular
 * firewall rules keep being the single source of truth.
 *
 *   GET  /api/deviceonboarding/permanent/list
 *   POST /api/deviceonboarding/permanent/add      mac, [ip], [host]
 *   POST /api/deviceonboarding/permanent/remove   mac
 */

namespace OPNsense\DeviceOnboarding\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\Backend;
use OPNsense\Core\Config;
use OPNsense\Dnsmasq\Dnsmasq;
use OPNsense\Firewall\Alias;
use OPNsense\DeviceOnboarding\DeviceOnboarding;

class PermanentController extends ApiControllerBase
{
    private static function normMac($mac)
    {
        $mac = strtolower(trim((string)$mac));
        return preg_match('/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/', $mac) ? $mac : null;
    }

    private static function listItems($field)
    {
        $items = [];
        foreach (preg_split('/[\n,]+/', (string)$field) as $item) {
            $item = trim($item);
            if ($item !== '') {
                $items[] = $item;
            }
        }
        return $items;
    }

    private static function sanitizeHost($host, $mac)
    {
        $host = strtolower(preg_replace('/[^A-Za-z0-9-]+/', '-', (string)$host));
        $host = trim(substr($host, 0, 63), '-');
        if ($host === '') {
            $host = 'device-' . str_replace(':', '', $mac);
        }
        return $host;
    }

    /**
     * Index the Dnsmasq reservations by MAC address and collect used IPs.
     */
    private static function reservations($dns)
    {
        $byMac = [];
        $usedIps = [];
        $usedHosts = [];
        foreach ($dns->hosts->iterateItems() as $uuid => $host) {
            $ips = self::listItems($host->ip);
            foreach ($ips as $ip) {
                $usedIps[$ip] = true;
            }
            $usedHosts[strtolower((string)$host->host)] = true;
            foreach (self::listItems($host->hwaddr) as $mac) {
                $byMac[strtolower($mac)] = [
                    'uuid' => $uuid,
                    'host' => (string)$host->host,
                    'domain' => (string)$host->domain,
                    'ip' => $ips[0] ?? '',
                    'node' => $host,
                ];
            }
        }
        return [$byMac, $usedIps, $usedHosts];
    }

    /**
     * Active Dnsmasq IPv4 leases: ip => mac. Dnsmasq does not hand out an
     * address that is still leased to another client, even if it is reserved.
     */
    private static function activeLeases()
    {
        $leases = [];
        $now = time();
        $data = @file_get_contents('/var/db/dnsmasq.leases');
        foreach (explode("\n", (string)$data) as $line) {
            $parts = preg_split('/\s+/', trim($line));
            if (count($parts) < 3 || $parts[0] === 'duid') {
                continue;
            }
            if (filter_var($parts[2], FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) === false) {
                continue;
            }
            if ((int)$parts[0] !== 0 && (int)$parts[0] < $now) {
                continue;
            }
            $leases[$parts[2]] = strtolower($parts[1]);
        }
        return $leases;
    }

    private function loadAliases($gs, $aliasMdl)
    {
        if ((string)$gs->alias_ipv4 === '' || (string)$gs->alias_mac === '') {
            return [null, null];
        }
        $v4 = $aliasMdl->getByName((string)$gs->alias_ipv4, true);
        $mac = $aliasMdl->getByName((string)$gs->alias_mac);
        return [$v4, $mac];
    }

    private static function applyChanges($dnsChanged, $aliasChanged)
    {
        $backend = new Backend();
        if ($dnsChanged) {
            /* same order as the core: stop, regenerate the configuration, start
             * ("dnsmasq restart" can start before the new configuration exists) */
            $backend->configdRun('dnsmasq stop');
            $backend->configdpRun('template reload', ['OPNsense/Dnsmasq']);
            $backend->configdRun('dnsmasq start');
        }
        if ($aliasChanged) {
            $backend->configdRun('template reload OPNsense/Filter');
            $backend->configdRun('filter refresh_aliases');
        }
    }

    public function listAction()
    {
        $gs = new DeviceOnboarding();
        $aliasMdl = new Alias();
        [$v4, $macAlias] = $this->loadAliases($gs, $aliasMdl);
        if ($v4 === null || $macAlias === null) {
            return ['status' => 'failed', 'message' => 'permanent access is not configured: select an existing IPv4 host alias and MAC alias in the settings', 'items' => []];
        }
        [$byMac] = self::reservations(new Dnsmasq());
        $v4Items = self::listItems($v4->content);
        $macItems = array_map('strtolower', self::listItems($macAlias->content));

        $rows = [];
        foreach ($macItems as $mac) {
            $res = $byMac[$mac] ?? null;
            $rows[$mac] = [
                'mac' => $mac,
                'host' => $res['host'] ?? '',
                'ip' => $res['ip'] ?? '',
                'ipv4' => $res !== null && in_array($res['ip'], $v4Items),
                'ipv6' => true,
            ];
        }
        /* IPv4 entries whose reservation is known but whose MAC is not in the MAC alias */
        foreach ($byMac as $mac => $res) {
            if (!isset($rows[$mac]) && $res['ip'] !== '' && in_array($res['ip'], $v4Items)) {
                $rows[$mac] = ['mac' => $mac, 'host' => $res['host'], 'ip' => $res['ip'], 'ipv4' => true, 'ipv6' => false];
            }
        }
        $rows = array_values($rows);
        usort($rows, function ($a, $b) {
            return ip2long($a['ip'] ?: '0.0.0.0') <=> ip2long($b['ip'] ?: '0.0.0.0');
        });
        return [
            'status' => 'ok',
            'alias_ipv4' => (string)$gs->alias_ipv4,
            'alias_mac' => (string)$gs->alias_mac,
            'items' => $rows,
        ];
    }

    public function addAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed', 'message' => 'POST required'];
        }
        $mac = self::normMac($this->request->getPost('mac'));
        if ($mac === null) {
            return ['status' => 'failed', 'message' => 'invalid MAC address'];
        }
        $wantIp = trim((string)$this->request->getPost('ip'));
        if ($wantIp !== '' && filter_var($wantIp, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) === false) {
            return ['status' => 'failed', 'message' => 'invalid IPv4 address'];
        }

        Config::getInstance()->lock();
        $gs = new DeviceOnboarding();
        $dns = new Dnsmasq();
        $aliasMdl = new Alias();
        [$v4, $macAlias] = $this->loadAliases($gs, $aliasMdl);
        if ($v4 === null || $macAlias === null) {
            Config::getInstance()->unlock();
            return ['status' => 'failed', 'message' => 'permanent access is not configured: select an existing IPv4 host alias and MAC alias in the settings'];
        }
        [$byMac, $usedIps, $usedHosts] = self::reservations($dns);
        $leases = self::activeLeases();
        if ($wantIp !== '' && isset($leases[$wantIp]) && $leases[$wantIp] !== $mac) {
            Config::getInstance()->unlock();
            return ['status' => 'failed', 'message' => "$wantIp is currently leased to " . $leases[$wantIp]];
        }

        /* hostname: request, else what the watcher saw in the lease */
        $host = trim((string)$this->request->getPost('host'));
        $currentIp = '';
        $seen = json_decode((string)(new Backend())->configdRun('deviceonboarding list'), true);
        foreach (array_merge($seen['open'] ?? [], $seen['seen'] ?? []) as $row) {
            if (strtolower($row['mac'] ?? '') === $mac) {
                $host = $host !== '' ? $host : ($row['host'] ?? '');
                $currentIp = $currentIp !== '' ? $currentIp : ($row['ip'] ?? '');
            }
        }

        $dnsChanged = false;
        $oldIp = '';
        if (isset($byMac[$mac])) {
            /* keep the existing reservation */
            $ip = $byMac[$mac]['ip'];
            $host = $byMac[$mac]['host'];
            if ($wantIp !== '' && $wantIp !== $ip) {
                if (isset($usedIps[$wantIp])) {
                    Config::getInstance()->unlock();
                    return ['status' => 'failed', 'message' => "$wantIp is already reserved"];
                }
                $byMac[$mac]['node']->ip->setValue($wantIp);
                $oldIp = $ip;
                $ip = $wantIp;
                $dnsChanged = true;
            }
        } else {
            $start = ip2long((string)$gs->reserve_start);
            $end = ip2long((string)$gs->reserve_end);
            if ($wantIp === '' && ($start === false || $end === false)) {
                Config::getInstance()->unlock();
                return ['status' => 'failed', 'message' => 'no reservation range configured, enter an IP address or set the range in the settings'];
            }
            if ($wantIp !== '') {
                if (isset($usedIps[$wantIp])) {
                    Config::getInstance()->unlock();
                    return ['status' => 'failed', 'message' => "$wantIp is already reserved"];
                }
                $ip = $wantIp;
            } else {
                $ip = '';
                for ($i = $start; $i !== false && $i <= $end; $i++) {
                    $candidate = long2ip($i);
                    if (!isset($usedIps[$candidate]) && (!isset($leases[$candidate]) || $leases[$candidate] === $mac)) {
                        $ip = $candidate;
                        break;
                    }
                }
                if ($ip === '') {
                    Config::getInstance()->unlock();
                    return ['status' => 'failed', 'message' => 'no free address in the reservation range'];
                }
            }
            $host = self::sanitizeHost($host, $mac);
            if (isset($usedHosts[$host])) {
                $host = substr($host, 0, 58) . '-' . substr(str_replace(':', '', $mac), -4);
            }
            $node = $dns->hosts->Add();
            $node->host->setValue($host);
            $node->domain->setValue((string)$gs->reserve_domain);
            $node->ip->setValue($ip);
            $node->hwaddr->setValue($mac);
            $node->descr->setValue('Device Onboarding: permanent access');
            $dnsChanged = true;
        }

        $aliasChanged = false;
        $v4Items = self::listItems($v4->content);
        if ($oldIp !== '' && in_array($oldIp, $v4Items)) {
            /* reservation moved: the old address loses its access */
            $v4Items = array_values(array_diff($v4Items, [$oldIp]));
            $v4->content->setValue(implode("\n", $v4Items));
            $aliasChanged = true;
        }
        if (!in_array($ip, $v4Items)) {
            $v4Items[] = $ip;
            $v4->content->setValue(implode("\n", $v4Items));
            $aliasChanged = true;
        }
        $macItems = self::listItems($macAlias->content);
        if (!in_array($mac, array_map('strtolower', $macItems))) {
            $macItems[] = $mac;
            $macAlias->content->setValue(implode("\n", $macItems));
            $aliasChanged = true;
        }

        try {
            if ($dnsChanged) {
                $dns->serializeToConfig();
            }
            if ($aliasChanged) {
                $aliasMdl->serializeToConfig();
            }
            if ($dnsChanged || $aliasChanged) {
                Config::getInstance()->save(['description' => "deviceonboarding: permanent access for $mac ($ip)"]);
            } else {
                Config::getInstance()->unlock();
            }
        } catch (\Exception $e) {
            Config::getInstance()->unlock();
            return ['status' => 'failed', 'message' => $e->getMessage()];
        }
        self::applyChanges($dnsChanged, $aliasChanged);

        /* the setup window is no longer needed */
        if ($currentIp !== '') {
            (new Backend())->configdpRun('deviceonboarding close', [$currentIp]);
        }

        return [
            'status' => 'ok',
            'mac' => $mac,
            'host' => $host,
            'ip' => $ip,
            'old_ip' => $oldIp,
            'reconnect' => ($currentIp !== '' && $currentIp !== $ip) || $oldIp !== '',
        ];
    }

    public function removeAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed', 'message' => 'POST required'];
        }
        $mac = self::normMac($this->request->getPost('mac'));
        if ($mac === null) {
            return ['status' => 'failed', 'message' => 'invalid MAC address'];
        }

        Config::getInstance()->lock();
        $gs = new DeviceOnboarding();
        $aliasMdl = new Alias();
        [$v4, $macAlias] = $this->loadAliases($gs, $aliasMdl);
        if ($v4 === null || $macAlias === null) {
            Config::getInstance()->unlock();
            return ['status' => 'failed', 'message' => 'permanent access is not configured: select an existing IPv4 host alias and MAC alias in the settings'];
        }
        [$byMac] = self::reservations(new Dnsmasq());

        $changed = false;
        $macItems = self::listItems($macAlias->content);
        $newMac = array_values(array_filter($macItems, function ($m) use ($mac) {
            return strtolower($m) !== $mac;
        }));
        if (count($newMac) !== count($macItems)) {
            $macAlias->content->setValue(implode("\n", $newMac));
            $changed = true;
        }
        $ip = $byMac[$mac]['ip'] ?? '';
        if ($ip !== '') {
            $v4Items = self::listItems($v4->content);
            $newV4 = array_values(array_diff($v4Items, [$ip]));
            if (count($newV4) !== count($v4Items)) {
                $v4->content->setValue(implode("\n", $newV4));
                $changed = true;
            }
        }

        try {
            if ($changed) {
                $aliasMdl->serializeToConfig();
                Config::getInstance()->save(['description' => "deviceonboarding: permanent access removed for $mac"]);
            } else {
                Config::getInstance()->unlock();
            }
        } catch (\Exception $e) {
            Config::getInstance()->unlock();
            return ['status' => 'failed', 'message' => $e->getMessage()];
        }
        self::applyChanges(false, $changed);
        /* drop running connections of the device */
        if ($changed && $ip !== '') {
            (new Backend())->configdpRun('deviceonboarding close', [$ip]);
        }
        return ['status' => $changed ? 'ok' : 'not_found', 'mac' => $mac, 'ip' => $ip];
    }
}
