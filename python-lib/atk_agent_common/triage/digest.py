"""Branded HTML digest for the daily triage sweep.

Pure rendering — no DSS imports, context in, HTML string out — so the digest
is unit-testable and previewable outside a scenario run. Email-safe HTML:
nested tables, inline styles, no external CSS/JS; the only remote asset is
the official Dataiku logo PNG (the same one the outreach emails use). Brand
palette sampled from that asset: teal #4DC9C3, ink #211C35.

Reading order is the agent's story: hero verdict → KPI tiles → what the
agent did → hosts that need attention → compact healthy strip. Deltas are
"vs yesterday" (previous sweep's stored score), never claimed as caused by
tonight's actions.

Context shape (the runnable's _digest_context / sample_context produce it):
  {dateLabel, timeLabel, runId, threshold, version?, toolkitUrl?,
   hosts: [sweep rows (+previousScore?)], flagged: [ids],
   hostLabels?: {id: label}, autoSummary?: {...run_auto_remediation summary},
   autoError?, configWarning?, snapshotError?, llmEnabled?, maxGb?}
"""
import html as _html

LOGO_URL = 'https://dku-assets.s3.amazonaws.com/img/emailing/DataikuLogoTeal_2025.png'

TEAL = '#4DC9C3'
TEAL_DARK = '#17948D'      # AA-contrast teal for text on white
INK = '#211C35'
INK_SOFT = '#4A4766'
GREY = '#8B87A0'
LINE = '#E6E4EE'
BG = '#F4F3F8'
CARD = '#FFFFFF'
AMBER = '#DE9426'
AMBER_BG = '#FCF4E4'
RED = '#D64A41'
RED_BG = '#FBEDEC'
TRACK = '#EDECF3'
HERO_TEXT = '#FEFEFF'      # near-white dodges Gmail dark-mode inversion

_FONT = ("'Source Sans 3',-apple-system,BlinkMacSystemFont,"
         "'Segoe UI',Arial,sans-serif")
_MONO = "'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"

# The Dataiku bird — a small footer accent only, so clients that strip inline
# SVG (Gmail) lose a decoration, never a load-bearing element.
_BIRD_SVG = (
    '<svg width="{s}" height="{s}" viewBox="0 0 71 71" fill="none" '
    'xmlns="http://www.w3.org/2000/svg" style="display:inline-block;'
    'vertical-align:middle;">'
    '<path d="M68.2984 48.7227H38.7461V54.1448H68.2984V48.7227Z" fill="{fill}"/>'
    '<path d="M65.5439 4.61992C64.0076 1.87173 61.0335 0 57.6053 0C52.6105 0 '
    '48.5617 3.97372 48.5617 8.87589C48.5617 9.34382 48.6071 9.7969 48.6828 '
    '10.2425L47.9184 11.1636L0.150326 69.0017C-0.0842763 69.2839 -0.0388693 '
    '69.6998 0.248708 69.9301C0.513582 70.1381 0.899541 70.1232 1.14171 '
    '69.8855L21.2494 50.1729C24.9577 46.5408 29.9827 44.4983 35.2272 '
    '44.4983H42.2274C57.522 44.4983 66.7169 35.9492 65.052 18.3831C64.4768 '
    '12.3371 64.7947 9.78947 66.9061 7.2344C67.9959 5.91973 70.1073 3.34981 '
    '70.1073 3.34981L67.6326 4.03314L65.5363 4.61249L65.5439 4.61992ZM57.7944 '
    '9.96773C56.4398 9.96773 55.3425 8.89074 55.3425 7.56121C55.3425 6.23169 '
    '56.4398 5.1547 57.7944 5.1547C59.1491 5.1547 60.2464 6.23169 60.2464 '
    '7.56121C60.2464 8.89074 59.1491 9.96773 57.7944 9.96773Z" fill="{fill}"/>'
    '</svg>')

_SEVERITY_COLOR = {'critical': RED, 'warning': AMBER, 'info': GREY}

_CATEGORY_LABELS = {
    'system_capacity': 'Capacity', 'runtime_config': 'Runtime config',
    'version_currency': 'Versions', 'connections': 'Connections',
    'code_envs': 'Code envs', 'project_footprint': 'Storage',
    'security_isolation': 'Security', 'jobs_scenarios': 'Jobs',
}


def _esc(value):
    return _html.escape(str(value if value is not None else ''), quote=True)


def _is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _score_visual(score, threshold):
    """(color, pill label) for a host score."""
    if not _is_num(score):
        return GREY, 'no score'
    if score < 50:
        return RED, 'critical'
    if score < threshold:
        return AMBER, 'attention'
    return TEAL_DARK, 'healthy'


def _host_label(ctx, host_id):
    labels = ctx.get('hostLabels') or {}
    if host_id in labels and labels[host_id]:
        return str(labels[host_id])
    if host_id in (None, '', 'local'):
        return 'Local DSS'
    return str(host_id)


def _delta_html(row, size=12):
    """'▲ +9 vs yesterday' when the sweep knows the previous score; an
    explicit '±0' when it is unchanged (so the omission never reads as
    unknown); nothing when there is no history."""
    prev = row.get('previousScore')
    cur = row.get('score')
    if not (_is_num(prev) and _is_num(cur)):
        return ''
    diff = round(cur) - round(prev)
    if diff == 0:
        return ('<span style="font-family:%s;font-size:11px;color:%s;'
                'white-space:nowrap;">&nbsp;&#177;0</span>' % (_FONT, GREY))
    arrow, color = ('&#9650;', TEAL_DARK) if diff > 0 else ('&#9660;', AMBER)
    return ('<span style="font-family:%s;font-size:%dpx;font-weight:700;color:%s;'
            'white-space:nowrap;">&nbsp;%s&nbsp;%+d</span>'
            '<span style="font-family:%s;font-size:11px;color:%s;'
            'white-space:nowrap;">&nbsp;vs yesterday</span>'
            % (_FONT, size, color, arrow, diff, _FONT, GREY))


def _chip(text, color=GREY, bg='#F1F0F6'):
    return ('<span style="display:inline-block;background:%s;color:%s;'
            'border-radius:10px;padding:2px 9px;font-size:11px;font-weight:600;'
            'font-family:%s;letter-spacing:.2px;">%s</span>'
            % (bg, color, _FONT, _esc(text)))


def _spacer(h):
    return ('<tr><td height="%d" style="height:%dpx;line-height:%dpx;'
            'font-size:1px;">&nbsp;</td></tr>' % (h, h, h))


def _section_title(title):
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0"><tr>'
            '<td style="font-family:%s;font-size:13px;font-weight:700;color:%s;'
            'text-transform:uppercase;letter-spacing:1.4px;">%s</td>'
            '</tr></table>' % (_FONT, GREY, _esc(title)))


def _kpi_cell(value, label, accent, first=False, last=False):
    pad = 'padding:0 %s 0 %s;' % ('0' if last else '5px', '0' if first else '5px')
    return ('<td width="25%%" valign="top" class="kpi-cell" style="%s">'
            '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="background:%s;border:1px solid %s;border-radius:10px;">'
            '<tr><td height="3" style="background:%s;border-radius:10px 10px 0 0;'
            'font-size:1px;line-height:3px;">&nbsp;</td></tr>'
            '<tr><td align="center" style="padding:14px 6px 3px 6px;font-family:%s;'
            'font-size:26px;font-weight:700;color:%s;letter-spacing:-.5px;'
            'white-space:nowrap;">%s</td></tr>'
            '<tr><td align="center" style="padding:0 6px 14px 6px;font-family:%s;'
            'font-size:10px;font-weight:600;color:%s;text-transform:uppercase;'
            'letter-spacing:1px;">%s</td></tr>'
            '</table></td>'
            % (pad, CARD, LINE, accent, _FONT, INK, value, _FONT, GREY, _esc(label)))


def _score_bar(score, color, height=6):
    pct = max(2, min(100, int(score))) if _is_num(score) else 2
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0">'
            '<tr><td style="background:%s;border-radius:4px;height:%dpx;font-size:1px;'
            'line-height:%dpx;"><table role="presentation" width="%d%%" cellpadding="0" '
            'cellspacing="0"><tr><td style="background:%s;border-radius:4px;height:%dpx;'
            'font-size:1px;line-height:%dpx;">&nbsp;</td></tr></table></td></tr></table>'
            % (TRACK, height, height, pct, color, height, height))


def _issue_row(issue):
    sev = str(issue.get('severity') or 'info')
    dot = _SEVERITY_COLOR.get(sev, GREY)
    return ('<tr><td valign="top" width="14" style="font-size:12px;line-height:19px;'
            'color:%s;">&#9679;</td>'
            '<td valign="top" style="font-family:%s;font-size:13px;line-height:19px;'
            'color:%s;padding:0 0 5px 2px;">%s</td></tr>'
            % (dot, _FONT, INK_SOFT, _esc(issue.get('title') or issue.get('id'))))


def _more_findings_row(row, ctx, shown):
    """Honest affordance when the host has more findings than the email shows."""
    total = int(row.get('criticalCount') or 0) + int(row.get('warningCount') or 0)
    extra = total - shown
    if extra <= 0:
        return ''
    label = '+%d more finding%s' % (extra, 's' if extra != 1 else '')
    url = ctx.get('toolkitUrl')
    inner = ('<a href="%s" target="_blank" style="color:%s;text-decoration:none;'
             'font-weight:600;">%s in Admin Toolkit &rarr;</a>'
             % (_esc(url), TEAL_DARK, label)) if url else \
        ('<span style="color:%s;">%s in Admin Toolkit</span>' % (GREY, label))
    return ('<tr><td width="14"></td><td style="font-family:%s;font-size:12px;'
            'padding:1px 0 5px 2px;">%s</td></tr>' % (_FONT, inner))


def _attention_card(ctx, row):
    threshold = ctx.get('threshold') or 75
    host = _host_label(ctx, row.get('host'))
    status = row.get('status')

    if status == 'error':
        err = row.get('error') or {}
        msg = err.get('message') if isinstance(err, dict) else str(err)
        return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
                'style="background:%s;border:1px solid %s;border-left:4px solid %s;'
                'border-radius:10px;"><tr><td style="padding:14px 18px 4px 18px;'
                'font-family:%s;font-size:15px;font-weight:700;color:%s;">%s'
                '&nbsp;&nbsp;%s</td></tr>'
                '<tr><td style="padding:0 18px 14px 18px;font-family:%s;font-size:13px;'
                'color:%s;line-height:19px;"><strong>Scoring failed.</strong> %s</td></tr>'
                '</table>'
                % (RED_BG, LINE, RED, _FONT, INK, _esc(host),
                   _chip('error', '#FFFFFF', RED), _FONT, RED,
                   _esc((msg or 'unknown error')[:220])))

    score = row.get('score')
    color, verdict = _score_visual(score, threshold)
    cats = row.get('categoryScores') or {}
    ranked = sorted((v, k) for k, v in cats.items() if _is_num(v))
    # only genuinely failing dimensions belong under "weakest"; when nothing
    # is below threshold (rare for a flagged host) show the single lowest.
    worst = [p for p in ranked if p[0] < threshold][:3] or ranked[:1]
    chips = '&nbsp;'.join(
        _chip('%s %d' % (_CATEGORY_LABELS.get(k, k), round(v)),
              RED if v < 50 else (AMBER if v < threshold else GREY))
        for v, k in worst)
    chips_html = ('<span style="font-family:%s;font-size:11px;color:%s;'
                  'text-transform:uppercase;letter-spacing:.8px;font-weight:600;">'
                  'weakest:</span>&nbsp; %s' % (_FONT, GREY, chips)) if chips else ''

    issues = (row.get('topIssues') or [])[:4]
    issues_html = ''
    if issues:
        issues_html = ('<tr><td style="padding:10px 18px 2px 18px;">'
                       '<table role="presentation" width="100%%" cellpadding="0" '
                       'cellspacing="0">%s%s</table></td></tr>'
                       % (''.join(_issue_row(i) for i in issues),
                          _more_findings_row(row, ctx, len(issues))))

    reco = (row.get('recommendation') or '').strip()
    reco_html = ''
    if reco and not reco.startswith('[LLM draft failed'):
        reco_html = ('<tr><td style="padding:6px 18px 14px 18px;">'
                     '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0">'
                     '<tr><td style="border-left:3px solid %s;background:#F6FBFB;'
                     'border-radius:0 8px 8px 0;padding:10px 14px;">'
                     '<div style="font-family:%s;font-size:10px;font-weight:700;color:%s;'
                     'text-transform:uppercase;letter-spacing:1px;padding-bottom:4px;">'
                     'Agent recommendation</div>'
                     '<div style="font-family:%s;font-size:13px;line-height:19px;color:%s;">'
                     '%s</div></td></tr></table></td></tr>'
                     % (TEAL, _FONT, TEAL_DARK, _FONT, INK_SOFT, _esc(reco)))
    else:
        reco_html = _spacer(12)

    score_display = '%d' % round(score) if _is_num(score) else '&mdash;'
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="background:%s;border:1px solid %s;border-radius:10px;">'
            '<tr><td style="padding:15px 18px 8px 18px;">'
            '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0"><tr>'
            '<td style="font-family:%s;font-size:15px;font-weight:700;color:%s;">%s</td>'
            '<td align="right" style="font-family:%s;font-size:13px;white-space:nowrap;">'
            '<span style="font-size:20px;font-weight:700;color:%s;">%s</span>'
            '<span style="color:%s;font-size:12px;">&nbsp;/100</span>%s&nbsp;&nbsp;%s</td>'
            '</tr></table></td></tr>'
            '<tr><td style="padding:0 18px;">%s</td></tr>'
            '<tr><td style="padding:9px 18px 0 18px;font-family:%s;font-size:12px;'
            'color:%s;">%s</td></tr>'
            '%s%s'
            '</table>'
            % (CARD, LINE, _FONT, INK, _esc(host), _FONT,
               color, score_display, GREY, _delta_html(row),
               _chip(verdict, '#FFFFFF', color),
               _score_bar(score, color),
               _FONT, GREY, chips_html,
               issues_html, reco_html))


def _healthy_strip(ctx, rows):
    """All above-threshold hosts as one compact card — single line each."""
    if not rows:
        return ''
    threshold = ctx.get('threshold') or 75
    body = []
    for i, row in enumerate(rows):
        color, _v = _score_visual(row.get('score'), threshold)
        border = 'border-top:1px solid %s;' % LINE if i else ''
        body.append(
            '<tr><td style="padding:10px 18px 10px 18px;%s">'
            '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0"><tr>'
            '<td width="180" style="font-family:%s;font-size:13px;font-weight:700;'
            'color:%s;">%s</td>'
            '<td class="healthy-bar" style="padding:0 14px;">%s</td>'
            '<td align="right" width="150" style="font-family:%s;font-size:13px;'
            'white-space:nowrap;"><span style="font-size:16px;font-weight:700;'
            'color:%s;">%d</span><span style="color:%s;font-size:11px;">&nbsp;/100'
            '</span>%s</td>'
            '</tr></table></td></tr>'
            % (border, _FONT, INK, _esc(_host_label(ctx, row.get('host'))),
               _score_bar(row.get('score'), color, height=5), _FONT,
               TEAL_DARK, round(row.get('score')), GREY, _delta_html(row)))
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="background:%s;border:1px solid %s;border-radius:10px;">%s</table>'
            % (CARD, LINE, ''.join(body)))


def _warming_note(ctx, rows):
    if not rows:
        return ''
    names = ', '.join(_esc(_host_label(ctx, r.get('host'))) for r in rows)
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="background:%s;border:1px solid %s;border-radius:10px;">'
            '<tr><td style="padding:12px 18px;font-family:%s;font-size:13px;color:%s;">'
            '%s&nbsp;&nbsp;<strong style="color:%s;">%s</strong> — heavy scans still '
            'warming; ranked in the next sweep.</td></tr></table>'
            % (CARD, LINE, _FONT, GREY, _chip('scan running', GREY), INK_SOFT, names))


def _auto_section(ctx):
    auto = ctx.get('autoSummary') or {}
    auto_error = ctx.get('autoError')
    executed = auto.get('executed') or []
    skipped = auto.get('skipped') or []
    enabled = auto.get('enabled') or []
    max_gb = ctx.get('maxGb')

    if auto_error:
        inner = ('<tr><td style="padding:14px 18px;font-family:%s;font-size:13px;'
                 'color:%s;line-height:19px;"><strong>Auto-remediation tier crashed:</strong> '
                 '%s</td></tr>' % (_FONT, RED, _esc(auto_error[:300])))
        return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
                'style="background:%s;border:1px solid %s;border-left:4px solid %s;'
                'border-radius:10px;">%s</table>' % (RED_BG, LINE, RED, inner))

    if auto.get('paused'):
        return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
                'style="background:%s;border:1px solid %s;border-radius:10px;">'
                '<tr><td style="padding:14px 18px;font-family:%s;font-size:13px;color:%s;'
                'line-height:19px;">Autonomous actions are <strong>paused</strong> — the '
                'agent observed and reported, but executed nothing. Re-enable them under '
                'Agents&nbsp;&rarr;&nbsp;Permissions.</td></tr></table>'
                % (AMBER_BG, LINE, _FONT, INK_SOFT))

    if not enabled:
        return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
                'style="background:%s;border:1px solid %s;border-radius:10px;">'
                '<tr><td style="padding:14px 18px;font-family:%s;font-size:13px;color:%s;'
                'line-height:19px;">No actions are opted into autonomous execution yet. '
                'The agent only observes and recommends — grant it capabilities under '
                'Agents&nbsp;&rarr;&nbsp;Permissions.</td></tr></table>'
                % (CARD, LINE, _FONT, GREY))

    rows = []
    for done in executed:
        no_effect = done.get('effect') == 'no-effect'
        freed = done.get('freedGB') or 0
        _right_td = ('<td align="right" valign="top" style="font-family:%s;'
                     'font-size:%dpx;font-weight:%s;color:%s;white-space:nowrap;'
                     'padding:12px 18px 10px 10px;border-bottom:1px solid ' + LINE + ';">'
                     '%s</td>')
        if freed:
            right = _right_td % (_FONT, 14, '700', TEAL_DARK, '%.2f&nbsp;GB' % freed)
        elif no_effect:
            right = _right_td % (_FONT, 12, '700', AMBER, '0 fixed')
        else:
            right = _right_td % (_FONT, 12, '400', GREY, 'done')
        detail_bits = []
        if done.get('detail'):
            detail_bits.append(_esc(done['detail']))
        if done.get('findingId'):
            detail_bits.append('finding <span style="font-family:%s;">%s</span>'
                               % (_MONO, _esc(done['findingId'])))
        if done.get('auditId') is not None:
            detail_bits.append('audit <span style="font-family:%s;">#%s</span>'
                               % (_MONO, _esc(done['auditId'])))
        detail_html = (' &nbsp;·&nbsp; '.join(detail_bits))
        warn_html = ''
        if done.get('warning'):
            warn_html = ('<div style="font-family:%s;font-size:12px;color:%s;'
                         'padding-top:3px;font-weight:600;">%s</div>'
                         % (_FONT, RED, _esc(done['warning'])))
        icon_bg, icon = (AMBER, '&#33;') if no_effect else (TEAL, '&#10003;')
        rows.append(
            '<tr>'
            '<td valign="top" width="24" style="padding:11px 0 11px 18px;'
            'border-bottom:1px solid {line};">'
            '<div style="width:18px;height:18px;border-radius:9px;background:{icon_bg};'
            'color:#FFFFFF;font-family:{font};font-size:12px;font-weight:700;'
            'text-align:center;line-height:18px;">{icon}</div></td>'
            '<td style="padding:10px 0 10px 10px;border-bottom:1px solid {line};">'
            '<span style="font-family:{mono};font-size:13px;font-weight:700;color:{ink};'
            'background:#F1F0F6;border-radius:6px;padding:2px 7px;">{action}</span>'
            '<span style="font-family:{font};font-size:13px;color:{ink_soft};">'
            '&nbsp; on {host}</span>'
            '<div style="font-family:{font};font-size:12px;color:{grey};'
            'padding-top:4px;">{detail}</div>'
            '{warn}</td>{right}</tr>'.format(
                line=LINE, icon_bg=icon_bg, font=_FONT, icon=icon, mono=_MONO,
                ink=INK, action=_esc(done.get('action')), ink_soft=INK_SOFT,
                host=_esc(_host_label(ctx, done.get('host'))), grey=GREY,
                detail=detail_html, warn=warn_html, right=right))

    if not executed:
        rows.append('<tr><td style="padding:14px 18px 4px 18px;font-family:%s;'
                    'font-size:13px;color:%s;line-height:19px;">'
                    'No matching findings today — nothing needed fixing. Opted-in: %s.'
                    '</td></tr>'
                    % (_FONT, GREY, _esc(', '.join(enabled))))

    skip_html = ''
    if skipped:
        shown = skipped[:8]
        items = ''.join(
            '<div style="padding:3px 0;color:%s;">&#8211;&nbsp; <span style="font-family:%s;'
            'color:%s;">%s</span>%s: %s</div>'
            % (GREY, _MONO, INK_SOFT, _esc(s.get('action') or 'all actions'),
               ' on ' + _esc(_host_label(ctx, s.get('host'))) if s.get('host') else '',
               _esc(str(s.get('reason') or '')[:180]))
            for s in shown)
        more = ('<div style="padding:3px 0;color:%s;">&#8230; and %d more</div>'
                % (GREY, len(skipped) - len(shown))) if len(skipped) > len(shown) else ''
        skip_html = ('<tr><td colspan="3" style="padding:10px 18px 14px 18px;">'
                     '<div style="font-family:%s;font-size:10px;font-weight:700;color:%s;'
                     'text-transform:uppercase;letter-spacing:1px;padding-bottom:4px;">'
                     'Held back</div>'
                     '<div style="font-family:%s;font-size:12px;line-height:17px;">%s%s</div>'
                     '</td></tr>' % (_FONT, GREY, _FONT, items, more))
    else:
        skip_html = _spacer(10)

    total_freed = auto.get('totalFreedGB') or 0
    meter_html = ''
    if _is_num(max_gb) and max_gb > 0:
        pct = max(0, min(100, int(round(100.0 * total_freed / max_gb))))
        meter_html = ('<tr><td colspan="3" style="padding:2px 18px 14px 18px;">'
                      '<table role="presentation" width="100%%" cellpadding="0" '
                      'cellspacing="0"><tr>'
                      '<td style="font-family:%s;font-size:11px;color:%s;'
                      'padding-bottom:4px;">Safety budget used: '
                      '<strong style="color:%s;">%.2f GB</strong> of %s GB</td>'
                      '</tr><tr><td>%s</td></tr></table></td></tr>'
                      % (_FONT, GREY, INK, total_freed, ('%g' % max_gb),
                         _score_bar(max(pct, 2), TEAL)))

    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="background:%s;border:1px solid %s;border-radius:10px;">'
            '%s%s%s</table>' % (CARD, LINE, ''.join(rows), skip_html, meter_html))


def _warnings_section(ctx):
    warnings = []
    if ctx.get('configWarning'):
        warnings.append(('Configuration', ctx['configWarning']))
    if ctx.get('snapshotError'):
        warnings.append(('Snapshot', 'Snapshot zip failed: %s' % ctx['snapshotError']))
    if not warnings:
        return ''
    items = ''.join(
        '<div style="padding:4px 0;font-family:%s;font-size:13px;line-height:19px;'
        'color:%s;"><strong>%s:</strong> %s</div>'
        % (_FONT, INK_SOFT, _esc(kind), _esc(str(text)[:300]))
        for kind, text in warnings)
    return (_spacer(18) +
            '<tr><td>' + _section_title('Needs your attention') + '</td></tr>' +
            _spacer(10) +
            '<tr><td><table role="presentation" width="100%%" cellpadding="0" '
            'cellspacing="0" style="background:%s;border:1px solid %s;border-left:4px '
            'solid %s;border-radius:10px;"><tr><td style="padding:12px 18px;">%s</td>'
            '</tr></table></td></tr>' % (AMBER_BG, LINE, AMBER, items))


def _fleet_average(hosts, key='score'):
    scores = [r[key] for r in hosts if _is_num(r.get(key))]
    return (sum(scores) / len(scores)) if scores else None


def build_subject(ctx):
    hosts = ctx.get('hosts') or []
    flagged = ctx.get('flagged') or []
    errored = [r for r in hosts if r.get('status') == 'error']
    scored = [r for r in hosts if _is_num(r.get('score'))]
    freed = float((ctx.get('autoSummary') or {}).get('totalFreedGB') or 0)
    freed_clause = ' — %.1f GB reclaimed overnight' % freed if freed >= 0.05 else ''
    if errored:
        return ('\U0001F534 Fleet triage: %d host%s failed scoring, %d scored%s'
                % (len(errored), 's' if len(errored) != 1 else '', len(scored), freed_clause))
    if flagged:
        scored_word = ' scored' if len(scored) != len(hosts) else ''
        return ('⚠️ %d of %d%s hosts need attention%s'
                % (len(flagged), len(scored), scored_word, freed_clause))
    return ('✅ Fleet healthy — %d host%s scored%s'
            % (len(scored), 's' if len(scored) != 1 else '', freed_clause))


def render_digest_html(ctx):
    hosts = ctx.get('hosts') or []
    flagged = set(ctx.get('flagged') or [])
    threshold = ctx.get('threshold') or 75
    auto = ctx.get('autoSummary') or {}
    executed = auto.get('executed') or []
    freed = float(auto.get('totalFreedGB') or 0)

    errored = [r for r in hosts if r.get('status') == 'error']
    warming = [r for r in hosts if r.get('status') == 'scan_running']
    scored = [r for r in hosts if _is_num(r.get('score'))]
    attention = [r for r in scored if r.get('host') in flagged] + errored
    healthy = [r for r in scored if r.get('host') not in flagged]

    warming_clause = (' %d host%s still warming up.'
                      % (len(warming), ' is' if len(warming) == 1 else 's are')) \
        if warming else ''
    if errored:
        verdict = ('%d host%s failed scoring — the fleet is not fully covered today.%s'
                   % (len(errored), 's' if len(errored) != 1 else '', warming_clause))
    elif flagged:
        verdict = ('%d of %d scored host%s below the health threshold and need%s '
                   'attention.%s'
                   % (len(flagged), len(scored), 's are' if len(flagged) != 1 else ' is',
                      '' if len(flagged) != 1 else 's', warming_clause))
    else:
        verdict = ('All %d scored host%s above the health threshold. '
                   'No action needed.%s'
                   % (len(scored), 's are' if len(scored) != 1 else ' is', warming_clause))

    # fleet average + honest same-host delta
    fleet_avg = _fleet_average(scored)
    both = [r for r in scored if _is_num(r.get('previousScore'))]
    fleet_delta_html = ''
    if both:
        prev_avg = _fleet_average(both, 'previousScore')
        cur_avg = _fleet_average(both)
        diff = round(cur_avg) - round(prev_avg)
        if diff:
            arrow, color = ('&#9650;', TEAL) if diff > 0 else ('&#9660;', '#F0B35C')
            fleet_delta_html = ('<div style="font-family:%s;font-size:12px;color:%s;'
                                'padding-top:5px;white-space:nowrap;">'
                                '<span style="color:%s;font-weight:700;">%s&nbsp;%+d</span> '
                                'vs yesterday</div>'
                                % (_FONT, '#C9C5DA', color, arrow, diff))
    fleet_block = ''
    if fleet_avg is not None:
        fleet_block = ('<td width="128" valign="middle" align="center" '
                       'class="fleet-cell" '
                       'style="padding:0 26px 0 0;">'
                       '<table role="presentation" cellpadding="0" cellspacing="0" '
                       'style="background:#2C2545;border-radius:12px;" width="128">'
                       '<tr><td align="center" style="padding:16px 8px 2px 8px;'
                       'font-family:%s;font-size:34px;font-weight:700;color:%s;'
                       'letter-spacing:-1px;">%d</td></tr>'
                       '<tr><td align="center" style="padding:0 8px 2px 8px;font-family:%s;'
                       'font-size:10px;font-weight:700;color:%s;text-transform:uppercase;'
                       'letter-spacing:1.4px;">Fleet average</td></tr>'
                       '<tr><td align="center" style="padding:0 8px 14px 8px;">%s</td></tr>'
                       '</table></td>'
                       % (_FONT, TEAL, round(fleet_avg), _FONT, '#9A94B8',
                          fleet_delta_html or '&nbsp;'))

    preheader = build_subject(ctx).replace('\U0001F534', '').replace(
        '⚠️', '').replace('✅', '').strip()

    scored_kpi = (str(len(hosts)) if len(scored) == len(hosts)
                  else '%d<span style="font-size:15px;color:%s;">&nbsp;/ %d</span>'
                  % (len(scored), GREY, len(hosts)))
    kpis = ('<tr>%s%s%s%s</tr>' % (
        _kpi_cell(scored_kpi, 'hosts scored', INK, first=True),
        _kpi_cell(str(len(flagged) + len(errored)), 'need attention',
                  AMBER if (flagged or errored) else TEAL),
        _kpi_cell(str(len(executed)), 'actions taken', TEAL),
        _kpi_cell(('%.2f' % freed) if freed else '0', 'GB reclaimed', TEAL, last=True)))

    attention_cards = ''
    if attention:
        attention_cards = ('<tr><td>' + _section_title(
            'Needs attention — worst first') + '</td></tr>' + _spacer(10))
        for i, row in enumerate(attention):
            if i:
                attention_cards += _spacer(10)
            attention_cards += '<tr><td>%s</td></tr>' % _attention_card(ctx, row)
        attention_cards += _spacer(24)

    healthy_block = ''
    if healthy:
        healthy_block = ('<tr><td>' + _section_title(
            'Healthy (%d)' % len(healthy)) + '</td></tr>' + _spacer(10) +
            '<tr><td>%s</td></tr>' % _healthy_strip(ctx, healthy))
        healthy_block += _spacer(10) if warming else _spacer(24)
    warming_block = ''
    if warming:
        warming_block = ('<tr><td>%s</td></tr>' % _warming_note(ctx, warming)) + _spacer(24)

    toolkit_btn = ''
    if ctx.get('toolkitUrl'):
        # padding lives on the anchor so the whole pill is clickable; the td
        # carries the background so Outlook still paints a button shape.
        toolkit_btn = ('<table role="presentation" cellpadding="0" cellspacing="0" '
                       'align="center" style="margin:0 auto;"><tr>'
                       '<td align="center" style="background:%s;border-radius:22px;">'
                       '<a href="%s" target="_blank" style="display:inline-block;'
                       'padding:11px 26px;font-family:%s;font-size:14px;font-weight:700;'
                       'color:%s;text-decoration:none;">Open Admin Toolkit &rarr;</a>'
                       '</td></tr></table>'
                       % (TEAL, _esc(ctx['toolkitUrl']), _FONT, INK))

    meta_bits = []
    if ctx.get('runId'):
        meta_bits.append('run <span style="font-family:%s;">%s</span>'
                         % (_MONO, _esc(ctx['runId'])))
    if ctx.get('version'):
        meta_bits.append('Admin Toolkit v%s' % _esc(ctx['version']))
    if ctx.get('digestNote'):
        meta_bits.append(_esc(ctx['digestNote']))

    return (
        '<!-- html:true -->\n'
        '<!DOCTYPE html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width">\n'
        '<meta name="color-scheme" content="light">\n'
        '<meta name="supported-color-schemes" content="light">\n'
        '<title>Daily Fleet Health</title>\n'
        '<style type="text/css">\n'
        '  @media only screen and (max-width: 540px) {\n'
        '    .kpi-cell { display: block !important; width: 100% !important; '
        'padding: 0 0 8px 0 !important; }\n'
        '    .healthy-bar { display: none !important; }\n'
        '    .fleet-cell { display: block !important; width: 100% !important; '
        'padding: 0 30px 22px 30px !important; }\n'
        '    .hero-cell { display: block !important; width: 100% !important; '
        'box-sizing: border-box !important; padding: 24px 26px 0 26px !important; }\n'
        '  }\n'
        '</style>\n'
        '</head>\n'
        '<body style="margin:0;padding:0;background:' + BG + ';">\n'
        '<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">'
        + _esc(preheader) + '&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;&zwnj;&nbsp;</div>\n'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:' + BG + ';">\n'
        '<tr><td align="center" style="padding:0 12px;">\n'
        '<table role="presentation" width="680" cellpadding="0" cellspacing="0" '
        'style="width:100%;max-width:680px;">\n'
        + _spacer(26) +
        # top bar: official logo left, product label right
        '<tr><td><table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td><img src="' + LOGO_URL + '" alt="Dataiku" width="106" '
        'style="display:block;width:106px;border:0;"></td>'
        '<td align="right" style="font-family:' + _FONT + ';font-size:11px;'
        'font-weight:700;color:' + GREY + ';text-transform:uppercase;'
        'letter-spacing:1.6px;">Admin Toolkit &middot; Agents</td>'
        '</tr></table></td></tr>\n'
        + _spacer(16) +
        # hero: verdict left, fleet-average block right
        '<tr><td style="background:' + INK + ';border-radius:14px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr>'
        '<td class="hero-cell" style="padding:26px 26px 24px 30px;">'
        '<div style="font-family:' + _FONT + ';font-size:11px;font-weight:700;'
        'color:' + TEAL + ';text-transform:uppercase;letter-spacing:2px;'
        'padding-bottom:9px;">Daily fleet triage &middot; ' + _esc(ctx.get('dateLabel') or '')
        + '</div>'
        '<div style="font-family:' + _FONT + ';font-size:27px;font-weight:700;'
        'color:' + HERO_TEXT + ';letter-spacing:-.4px;line-height:33px;">'
        'Fleet Health Report</div>'
        '<div style="font-family:' + _FONT + ';font-size:14px;color:#C9C5DA;'
        'line-height:21px;padding-top:8px;">' + _esc(verdict) + '</div>'
        '<div style="padding-top:14px;">'
        + _chip('sweep %s' % (ctx.get('timeLabel') or ''), HERO_TEXT, '#38324F')
        + '&nbsp;' + _chip('threshold %s' % threshold, HERO_TEXT, '#38324F')
        + ('&nbsp;' + _chip('LLM recommendations on', TEAL, '#38324F')
           if ctx.get('llmEnabled') else '')
        + '</div></td>'
        + fleet_block +
        '</tr></table></td></tr>\n'
        + _spacer(14) +
        # KPI row
        '<tr><td><table role="presentation" width="100%" cellpadding="0" '
        'cellspacing="0">' + kpis + '</table></td></tr>\n'
        + _spacer(24) +
        '<tr><td>' + _section_title('What the agent did while you slept') + '</td></tr>\n'
        + _spacer(10) +
        '<tr><td>' + _auto_section(ctx) + '</td></tr>\n'
        + _spacer(24) +
        attention_cards +
        healthy_block +
        warming_block +
        _warnings_section(ctx) +
        _spacer(4) +
        ('<tr><td>' + toolkit_btn + '</td></tr>' + _spacer(24) if toolkit_btn else '') +
        # footer
        '<tr><td style="border-top:1px solid ' + LINE + ';"></td></tr>\n'
        + _spacer(16) +
        '<tr><td align="center" style="font-family:' + _FONT + ';font-size:12px;'
        'color:#767191;line-height:19px;">'
        + _BIRD_SVG.format(s=16, fill=TEAL) +
        '&nbsp; Generated autonomously by the <strong style="color:' + INK_SOFT + ';">'
        'Admin Toolkit daily agent</strong> on your Dataiku DSS fleet.<br>'
        + ' &nbsp;&middot;&nbsp; '.join(meta_bits) +
        '</td></tr>\n'
        + _spacer(6) +
        '<tr><td align="center" style="font-family:' + _FONT + ';font-size:11px;'
        'color:#B4B0C4;">You receive this because you are the configured triage '
        'recipient. Delivery settings live in the Admin Toolkit webapp.</td></tr>\n'
        + _spacer(30) +
        '</table>\n</td></tr>\n</table>\n</body>\n</html>\n')


def render_digest_text(ctx):
    """Plain-text twin — the fallback body and the scenario-log copy."""
    hosts = ctx.get('hosts') or []
    lines = ['Daily DSS fleet health triage (threshold %s):' % (ctx.get('threshold') or 75), '']
    if ctx.get('configWarning'):
        lines += ['CONFIG WARNING: %s' % ctx['configWarning'], '']
    if ctx.get('snapshotError'):
        lines += ['SNAPSHOT WARNING: snapshot zip failed: %s' % ctx['snapshotError'], '']
    auto = ctx.get('autoSummary') or {}
    if ctx.get('autoError'):
        lines += ['AUTO-REMEDIATION WARNING: tier crashed: %s' % ctx['autoError'], '']
    elif auto.get('enabled'):
        lines.append('Auto-remediation (opted-in: %s%s):'
                     % (', '.join(auto['enabled']),
                        '; PAUSED' if auto.get('paused') else ''))
        for done in auto.get('executed') or []:
            lines.append('  + %s %s (finding %s) — freed %.2f GB, audit #%s'
                         % (done.get('host'), done.get('action'), done.get('findingId'),
                            done.get('freedGB') or 0, done.get('auditId')))
            if done.get('warning'):
                lines.append('    !! %s' % done['warning'])
        for skip in auto.get('skipped') or []:
            lines.append('  - %s %s: %s' % (skip.get('host'),
                                            skip.get('action') or '(all)',
                                            skip.get('reason')))
        if not (auto.get('executed') or auto.get('skipped')):
            lines.append('  (no matching findings today)')
        lines += ['  Total freed: %.2f GB across %d object(s).'
                  % (auto.get('totalFreedGB') or 0, auto.get('totalObjects') or 0), '']
    for row in hosts:
        score = row.get('score')
        lines.append('%s — %s (%s)' % (row.get('host'),
                                       ('score %s' % score) if score is not None else 'no score',
                                       row.get('status')))
        if row.get('error'):
            lines.append('  ! %s' % str(row['error'])[:300])
        if row.get('recommendation'):
            lines.append('  > %s' % row['recommendation'])
    return '\n'.join(lines)


def sample_context():
    """Representative demo payload — what a real morning looks like on a
    six-host fleet with two hosts drifting and an overnight cleanup. One
    clock: Sunday 2026-07-19, sweep 07:00 CEST → epoch 1784437200."""
    return {
        'dateLabel': 'Sunday, July 19',
        'timeLabel': '07:00 server time',
        'runId': 'triage-1784437200',
        'threshold': 75,
        'version': None,
        'llmEnabled': True,
        'maxGb': 20,
        'hostLabels': {'local': 'dss-design-emea', 'prod-us': 'dss-prod-us',
                       'prod-emea': 'dss-prod-emea', 'automation': 'dss-automation',
                       'scoring': 'dss-scoring', 'sandbox': 'dss-sandbox'},
        'hosts': [
            {'host': 'prod-emea', 'score': 46, 'previousScore': 41, 'status': 'ok',
             'categoryScores': {'system_capacity': 31, 'connections': 40,
                                'runtime_config': 78, 'version_currency': 85},
             'criticalCount': 2, 'warningCount': 3,
             'topIssues': [
                 {'id': 'disk-critical-/data', 'severity': 'critical',
                  'title': 'Disk 93% full on /data'},
                 {'id': 'cap-connection-broken', 'severity': 'critical',
                  'title': '2 actively-used connections failing their test',
                  'items': ['snowflake_prod', 'redshift_marts']},
                 {'id': 'sanity-warning-JUPYTER_KERNELS', 'severity': 'warning',
                  'title': 'Sanity check warning: 14 long-running notebook kernels'},
             ],
             'recommendation': 'The /data mount is one busy weekend from full: rotated '
                               'logs and the Docker builder cache were reclaimed tonight, '
                               'but snowflake_prod and redshift_marts still fail their '
                               'tests and feed 11 projects with active scenario triggers. '
                               'Repair those two connections first. Evidence: '
                               'disk-critical-/data at 93%, cap-connection-broken '
                               '(2 connections, 11 active-trigger projects).'},
            {'host': 'automation', 'score': 68, 'previousScore': 63, 'status': 'ok',
             'categoryScores': {'system_capacity': 55, 'runtime_config': 62,
                                'connections': 90, 'version_currency': 71},
             'criticalCount': 0, 'warningCount': 4,
             'topIssues': [
                 {'id': 'disk-warning-/data', 'severity': 'warning',
                  'title': 'Disk 84% full on /data'},
                 {'id': 'python-lifecycle-plan', 'severity': 'warning',
                  'title': '3 in-use code envs on Python 3.8 (deprecated in DSS 14)'},
                 {'id': 'features-disabled-several', 'severity': 'warning',
                  'title': '4 platform features disabled'},
             ],
             'recommendation': 'Disk pressure is early-stage: aged job logs were trimmed '
                               'tonight and bought roughly three weeks of headroom. Use it '
                               'to migrate the three Python 3.8 code envs before the DSS 15 '
                               'upgrade window. Evidence: disk-warning-/data at 84%, '
                               'python-lifecycle-plan (3 envs).'},
            {'host': 'prod-us', 'score': 88, 'previousScore': 88, 'status': 'ok',
             'categoryScores': {'system_capacity': 92, 'connections': 100,
                                'runtime_config': 81, 'version_currency': 90},
             'criticalCount': 0, 'warningCount': 1, 'topIssues': []},
            {'host': 'local', 'score': 91, 'previousScore': 89, 'status': 'ok',
             'categoryScores': {'system_capacity': 95, 'connections': 100,
                                'runtime_config': 84, 'version_currency': 93},
             'criticalCount': 0, 'warningCount': 1, 'topIssues': []},
            {'host': 'scoring', 'score': 94, 'previousScore': 94, 'status': 'ok',
             'categoryScores': {'system_capacity': 97, 'connections': 100,
                                'runtime_config': 89, 'version_currency': 95},
             'criticalCount': 0, 'warningCount': 0, 'topIssues': []},
            {'host': 'sandbox', 'score': None, 'status': 'scan_running'},
        ],
        'flagged': ['prod-emea', 'automation'],
        'autoSummary': {
            'enabled': ['connection-test', 'docker-prune', 'job-logs-cleanup',
                        'log-cleanup'],
            'paused': False, 'remoteHosts': True,
            'executed': [
                {'host': 'prod-emea', 'action': 'log-cleanup',
                 'findingId': 'disk-critical-/data', 'freedGB': 1.42, 'auditId': 3121,
                 'detail': '312 rotated log files removed'},
                {'host': 'prod-emea', 'action': 'docker-prune',
                 'findingId': 'disk-critical-/data', 'freedGB': 3.86, 'auditId': 3122,
                 'detail': 'docker reported 3.86 GB reclaimed from the builder cache'},
                {'host': 'prod-emea', 'action': 'connection-test',
                 'findingId': 'cap-connection-broken', 'freedGB': 0, 'auditId': 3123,
                 'detail': '0 connections recovered, 2 still failing',
                 'effect': 'no-effect'},
                {'host': 'automation', 'action': 'job-logs-cleanup',
                 'findingId': 'disk-warning-/data', 'freedGB': 2.19, 'auditId': 3124,
                 'detail': '57 aged job directories removed'},
            ],
            'skipped': [
                {'host': 'prod-emea', 'action': 'notebook-kernels-shutdown',
                 'findingId': 'sanity-warning-JUPYTER_KERNELS',
                 'reason': 'not opted into autonomous execution '
                           '(Permissions → Autonomous agent)'},
            ],
            'totalFreedGB': 7.47, 'totalObjects': 372,
        },
        'autoError': None,
        'configWarning': None,
        'snapshotError': None,
    }
