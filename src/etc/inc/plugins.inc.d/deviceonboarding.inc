<?php

/*
 * Device Onboarding – plugin hooks.
 *
 * The watcher keeps its rule and its tables inside the pf anchor
 * "deviceonboarding". This hook only makes sure the anchor is referenced from
 * the main ruleset while the service is enabled.
 */

function deviceonboarding_enabled()
{
    $mdl = new \OPNsense\DeviceOnboarding\DeviceOnboarding();
    return (string)$mdl->enabled == '1';
}

function deviceonboarding_services()
{
    $services = [];

    if (deviceonboarding_enabled()) {
        $services[] = [
            'description' => gettext('Device Onboarding'),
            'configd' => [
                'restart' => ['deviceonboarding restart'],
                'start' => ['deviceonboarding start'],
                'stop' => ['deviceonboarding stop'],
            ],
            'name' => 'deviceonboarding',
            'pidfile' => '/var/run/deviceonboarding.pid',
        ];
    }

    return $services;
}

function deviceonboarding_firewall($fw)
{
    if (deviceonboarding_enabled()) {
        $fw->registerAnchor('deviceonboarding', 'fw');
    }
}
