<script>
    'use strict';

    function fmtTime(ts) {
        if (!ts) { return ''; }
        return new Date(ts * 1000).toLocaleString();
    }

    function fmtRemaining(sec) {
        sec = Math.max(0, Math.round(sec));
        return Math.floor(sec / 60) + ':' + ('0' + (sec % 60)).slice(-2);
    }

    function escapeHtml(s) {
        return $('<div/>').text(s === undefined || s === null ? '' : String(s)).html();
    }

    function btn(cls, attrs, icon, title) {
        let a = '';
        Object.keys(attrs).forEach(function (k) { a += ' data-' + k + '="' + escapeHtml(attrs[k]) + '"'; });
        return '<button type="button" class="btn btn-xs ' + cls + '"' + a + ' title="' + escapeHtml(title) + '">' +
               '<i class="fa ' + icon + ' fa-fw"></i></button> ';
    }

    function showResult(data, okText) {
        if (data.status === 'ok') {
            if (okText) {
                BootstrapDialog.show({type: BootstrapDialog.TYPE_INFO, title: 'Device Onboarding', message: okText});
            }
        } else {
            BootstrapDialog.show({type: BootstrapDialog.TYPE_WARNING, title: 'Device Onboarding',
                                  message: escapeHtml(data.message || data.status || 'failed')});
        }
    }

    function loadStatus() {
        $.when(
            $.ajax({url: '/api/deviceonboarding/service/list', dataType: 'json'}),
            $.ajax({url: '/api/deviceonboarding/permanent/list', dataType: 'json'})
        ).done(function (r1, r2) {
            const data = r1[0], perm = r2[0];
            const permMacs = {};
            (perm.items || []).forEach(function (p) { permMacs[p.mac] = p; });

            $('#gs_error').toggle(!!data.error).text(data.error || '');
            const openBody = $('#gs_open tbody').empty();
            (data.open || []).forEach(function (row) {
                openBody.append($('<tr/>').append(
                    $('<td/>').text(row.ip),
                    $('<td/>').html((row.ip6 || []).map(escapeHtml).join('<br/>') || '<span class="text-muted">–</span>'),
                    $('<td/>').text(row.mac), $('<td/>').text(row.host || ''),
                    $('<td/>').text(fmtRemaining(row.remaining)),
                    $('<td/>').html(
                        btn('btn-default gs-extend', {ip: row.ip}, 'fa-plus', '{{ lang._('Extend') }}') +
                        (permMacs[row.mac] ? '' : btn('btn-success gs-permanent', {mac: row.mac, host: row.host || ''}, 'fa-star', '{{ lang._('Grant permanent access') }}')) +
                        btn('btn-danger gs-close', {ip: row.ip}, 'fa-times', '{{ lang._('Close now') }}')
                    )
                ));
            });
            if (!(data.open || []).length) {
                openBody.append('<tr><td colspan="6">{{ lang._('No open windows.') }}</td></tr>');
            }

            const seenBody = $('#gs_seen tbody').empty();
            (data.seen || []).forEach(function (row) {
                let note = row.baseline ? '{{ lang._('known at install') }}' : (row.opened ? '{{ lang._('window granted') }}' : '');
                if (permMacs[row.mac]) {
                    note = '<span class="label label-success">{{ lang._('permanent') }}</span> ' + escapeHtml(note);
                } else {
                    note = escapeHtml(note);
                }
                seenBody.append($('<tr/>').append(
                    $('<td/>').text(row.mac), $('<td/>').text(row.ip || ''), $('<td/>').text(row.host || ''),
                    $('<td/>').text(fmtTime(row.first)), $('<td/>').html(note),
                    $('<td/>').html(
                        (permMacs[row.mac] ? '' : btn('btn-success gs-permanent', {mac: row.mac, host: row.host || ''}, 'fa-star', '{{ lang._('Grant permanent access') }}')) +
                        btn('btn-default gs-forget', {mac: row.mac}, 'fa-eraser', '{{ lang._('Forget') }}')
                    )
                ));
            });

            const permBody = $('#gs_perm tbody').empty();
            $('#gs_perm_aliases').text((perm.alias_ipv4 || '') + ' / ' + (perm.alias_mac || ''));
            if (perm.status !== 'ok') {
                permBody.append($('<tr/>').append($('<td colspan="6"/>').text(perm.message || 'error')));
            }
            (perm.items || []).forEach(function (p) {
                permBody.append($('<tr/>').append(
                    $('<td/>').text(p.ip || '–'), $('<td/>').text(p.mac), $('<td/>').text(p.host || ''),
                    $('<td/>').html(p.ipv4 ? '<i class="fa fa-check text-success"></i>' : '<i class="fa fa-times text-danger"></i>'),
                    $('<td/>').html(p.ipv6 ? '<i class="fa fa-check text-success"></i>' : '<i class="fa fa-times text-danger"></i>'),
                    $('<td/>').html(btn('btn-danger gs-perm-remove', {mac: p.mac, host: p.host || p.mac}, 'fa-trash', '{{ lang._('Remove permanent access') }}'))
                ));
            });
        });
    }

    function grantPermanent(mac, host, ip) {
        ajaxCall('/api/deviceonboarding/permanent/add', {'mac': mac, 'host': host || '', 'ip': ip || ''}, function (data) {
            let msg = '';
            if (data.status === 'ok') {
                msg = escapeHtml(data.host) + ' (' + escapeHtml(data.mac) + ') → ' + escapeHtml(data.ip);
                if (data.reconnect) {
                    msg += '<br/>{{ lang._('The device gets the new address when it reconnects or renews its lease (restart it to speed this up).') }}';
                }
            }
            showResult(data, msg);
            loadStatus();
        });
    }

    $(document).ready(function () {
        mapDataToFormUI({'frm_general_settings': '/api/deviceonboarding/settings/get'}).done(function () {
            formatTokenizersUI();
            $('.selectpicker').selectpicker('refresh');
            updateServiceControlUI('deviceonboarding');
        });

        $('#reconfigureAct').SimpleActionButton({
            onPreAction: function () {
                const dfObj = new $.Deferred();
                saveFormToEndpoint('/api/deviceonboarding/settings/set', 'frm_general_settings', function () {
                    dfObj.resolve();
                }, true, function () {
                    dfObj.reject();
                });
                return dfObj;
            },
            onAction: function () {
                updateServiceControlUI('deviceonboarding');
                loadStatus();
            }
        });

        $(document).on('click', '.gs-close', function () {
            ajaxCall('/api/deviceonboarding/service/close/' + $(this).data('ip'), {}, loadStatus);
        });
        $(document).on('click', '.gs-extend', function () {
            ajaxCall('/api/deviceonboarding/service/open', {'ip': $(this).data('ip'), 'minutes': 0}, loadStatus);
        });
        $(document).on('click', '.gs-forget', function () {
            ajaxCall('/api/deviceonboarding/service/forget/' + $(this).data('mac'), {}, loadStatus);
        });
        $(document).on('click', '.gs-permanent', function () {
            const mac = $(this).data('mac'), host = $(this).data('host');
            BootstrapDialog.confirm({
                title: '{{ lang._('Grant permanent access') }}',
                message: '{{ lang._('Create a fixed DHCP reservation and add the device to the internet aliases?') }}<br/><b>' +
                         escapeHtml(host || '') + ' ' + escapeHtml(mac) + '</b>',
                type: BootstrapDialog.TYPE_WARNING,
                btnOKLabel: '{{ lang._('Grant') }}',
                callback: function (ok) { if (ok) { grantPermanent(mac, host, ''); } }
            });
        });
        $(document).on('click', '.gs-perm-remove', function () {
            const mac = $(this).data('mac'), host = $(this).data('host');
            BootstrapDialog.confirm({
                title: '{{ lang._('Remove permanent access') }}',
                message: '{{ lang._('Remove the device from both internet aliases? The DHCP reservation is kept.') }}<br/><b>' +
                         escapeHtml(host) + ' ' + escapeHtml(mac) + '</b>',
                type: BootstrapDialog.TYPE_DANGER,
                btnOKLabel: '{{ lang._('Remove') }}',
                callback: function (ok) {
                    if (ok) {
                        ajaxCall('/api/deviceonboarding/permanent/remove', {'mac': mac}, function (data) {
                            showResult(data, '');
                            loadStatus();
                        });
                    }
                }
            });
        });
        $('#gs_open_btn').click(function () {
            ajaxCall('/api/deviceonboarding/service/open', {
                'ip': $('#gs_open_ip').val(), 'minutes': $('#gs_open_minutes').val()
            }, function (data) { showResult(data, ''); loadStatus(); });
        });
        $('#gs_perm_btn').click(function () {
            grantPermanent($('#gs_perm_mac').val(), $('#gs_perm_host').val(), $('#gs_perm_ip').val());
        });

        loadStatus();
        setInterval(loadStatus, 15000);
    });
</script>

<div class="content-box">
    {{ partial("layout_partials/base_form", ['fields': generalForm, 'id': 'frm_general_settings']) }}
    <div class="col-md-12">
        <hr/>
        <button class="btn btn-primary" id="reconfigureAct"
                data-endpoint="/api/deviceonboarding/service/reconfigure"
                data-label="{{ lang._('Apply') }}"
                data-error-title="{{ lang._('Error reconfiguring Device Onboarding') }}"
                type="button"></button>
        <br/><br/>
    </div>
</div>

<div class="content-box" style="margin-top: 15px; padding: 10px;">
    <h2>{{ lang._('Open windows') }}</h2>
    <div id="gs_error" class="alert alert-warning" style="display: none;"></div>
    <table id="gs_open" class="table table-condensed table-striped">
        <thead><tr>
            <th>{{ lang._('IPv4 address') }}</th><th>{{ lang._('IPv6 addresses') }}</th><th>{{ lang._('MAC address') }}</th>
            <th>{{ lang._('Hostname') }}</th><th>{{ lang._('Remaining') }}</th><th></th>
        </tr></thead>
        <tbody></tbody>
    </table>
    <div class="form-inline">
        <input type="text" id="gs_open_ip" class="form-control" placeholder="{{ lang._('IPv4 address') }}" style="width: 180px;"/>
        <input type="number" id="gs_open_minutes" class="form-control" min="1" max="1440" placeholder="{{ lang._('minutes') }}" style="width: 110px;"/>
        <button class="btn btn-default" id="gs_open_btn" type="button"><i class="fa fa-unlock fa-fw"></i> {{ lang._('Open window manually') }}</button>
    </div>
</div>

<div class="content-box" style="margin-top: 15px; padding: 10px;">
    <h2>{{ lang._('Permanent access') }} <small id="gs_perm_aliases"></small></h2>
    <p class="text-muted">{{ lang._('Devices in the firewall aliases for permanent internet access. The aliases stay the single source of truth; this list only reads and edits them.') }}</p>
    <table id="gs_perm" class="table table-condensed table-striped">
        <thead><tr>
            <th>{{ lang._('Reserved IP') }}</th><th>{{ lang._('MAC address') }}</th><th>{{ lang._('Hostname') }}</th>
            <th>IPv4</th><th>IPv6</th><th></th>
        </tr></thead>
        <tbody></tbody>
    </table>
    <div class="form-inline">
        <input type="text" id="gs_perm_mac" class="form-control" placeholder="{{ lang._('MAC address') }}" style="width: 180px;"/>
        <input type="text" id="gs_perm_host" class="form-control" placeholder="{{ lang._('hostname (optional)') }}" style="width: 180px;"/>
        <input type="text" id="gs_perm_ip" class="form-control" placeholder="{{ lang._('IP (optional)') }}" style="width: 150px;"/>
        <button class="btn btn-success" id="gs_perm_btn" type="button"><i class="fa fa-star fa-fw"></i> {{ lang._('Grant permanent access') }}</button>
    </div>
</div>

<div class="content-box" style="margin-top: 15px; padding: 10px;">
    <h2>{{ lang._('Known devices') }}</h2>
    <p class="text-muted">{{ lang._('MAC addresses already seen in the pool. They do not get another automatic window. This is not the list of permanent access.') }}</p>
    <table id="gs_seen" class="table table-condensed table-striped">
        <thead><tr>
            <th>{{ lang._('MAC address') }}</th><th>{{ lang._('Last IP') }}</th><th>{{ lang._('Hostname') }}</th>
            <th>{{ lang._('First seen') }}</th><th>{{ lang._('Note') }}</th><th></th>
        </tr></thead>
        <tbody></tbody>
    </table>
</div>
