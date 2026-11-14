<?php

namespace OPNsense\DeviceOnboarding\Api;

use OPNsense\Base\ApiMutableModelControllerBase;

class SettingsController extends ApiMutableModelControllerBase
{
    protected static $internalModelName = 'deviceonboarding';
    protected static $internalModelClass = '\OPNsense\DeviceOnboarding\DeviceOnboarding';
}
