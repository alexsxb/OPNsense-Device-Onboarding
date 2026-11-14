<?php

namespace OPNsense\DeviceOnboarding\Api;

use OPNsense\Base\ApiMutableServiceControllerBase;
use OPNsense\Core\Backend;

class ServiceController extends ApiMutableServiceControllerBase
{
    protected static $internalServiceClass = '\OPNsense\DeviceOnboarding\DeviceOnboarding';
    protected static $internalServiceTemplate = 'OPNsense/DeviceOnboarding';
    protected static $internalServiceEnabled = 'enabled';
    protected static $internalServiceName = 'deviceonboarding';

    /**
     * Apply settings: regenerate templates, restart the watcher and reload
     * the firewall so the "deviceonboarding" anchor is (un)registered.
     */
    public function reconfigureAction()
    {
        $result = parent::reconfigureAction();
        if ($this->request->isPost()) {
            (new Backend())->configdRun('filter reload');
        }
        return $result;
    }

    /**
     * Currently open windows and all devices seen so far.
     */
    public function listAction()
    {
        $response = (new Backend())->configdRun('deviceonboarding list');
        $data = json_decode((string)$response, true);
        return is_array($data) ? $data : ['open' => [], 'seen' => [], 'error' => trim((string)$response)];
    }

    /**
     * Open (or extend) a window for an IPv4 address.
     */
    public function openAction()
    {
        if (!$this->request->isPost()) {
            return ['status' => 'failed', 'message' => 'POST required'];
        }
        $ip = (string)$this->request->getPost('ip');
        $minutes = (int)$this->request->getPost('minutes');
        if (filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) === false) {
            return ['status' => 'failed', 'message' => 'invalid IPv4 address'];
        }
        if ($minutes < 1 || $minutes > 1440) {
            $minutes = 0; /* 0 = use configured duration */
        }
        return $this->runCtl('deviceonboarding open', [$ip, (string)$minutes]);
    }

    /**
     * Close a window immediately.
     */
    public function closeAction($ip = null)
    {
        if (!$this->request->isPost() || filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) === false) {
            return ['status' => 'failed', 'message' => 'POST with valid IPv4 address required'];
        }
        return $this->runCtl('deviceonboarding close', [$ip]);
    }

    /**
     * Forget a MAC address, so it gets a new window when it shows up again.
     */
    public function forgetAction($mac = null)
    {
        $mac = strtolower((string)$mac);
        if (!$this->request->isPost() || !preg_match('/^([0-9a-f]{2}:){5}[0-9a-f]{2}$/', $mac)) {
            return ['status' => 'failed', 'message' => 'POST with valid MAC address required'];
        }
        return $this->runCtl('deviceonboarding forget', [$mac]);
    }

    private function runCtl($action, $params)
    {
        $response = (new Backend())->configdpRun($action, $params);
        $data = json_decode((string)$response, true);
        return is_array($data) ? $data : ['status' => 'failed', 'message' => trim((string)$response)];
    }
}
