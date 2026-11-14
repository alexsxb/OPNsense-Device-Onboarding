<?php

/*
 * Device Onboarding – temporary internet access for newly seen devices.
 */

namespace OPNsense\DeviceOnboarding;

use OPNsense\Base\BaseModel;
use OPNsense\Base\Messages\Message;

class DeviceOnboarding extends BaseModel
{
    /**
     * Interface and pool are required when enabled; pool start must not be greater than pool end.
     */
    public function performValidation($validateFullModel = false)
    {
        $messages = parent::performValidation($validateFullModel);
        if ((string)$this->enabled == '1') {
            foreach (['interface', 'pool_start', 'pool_end'] as $field) {
                if ((string)$this->$field === '') {
                    $messages->appendMessage(new Message(gettext('Required when enabled.'), $field));
                }
            }
        }
        $start = ip2long((string)$this->pool_start);
        $end = ip2long((string)$this->pool_end);
        if ($start !== false && $end !== false && $start > $end) {
            $messages->appendMessage(new Message(
                gettext('Pool start must be lower than or equal to pool end.'),
                'pool_end'
            ));
        }
        return $messages;
    }
}
