"""Email outreach tools — campaign preview/send for DSS health findings.

Mail-channel resolution lives in adk_backend.mail (shared with feedback and
backend.py's /api/mail-channels). NOTE: this plumbing is LIVE — it is used by
FS Migration + Projects Cleaner outreach flows.
"""
import logging
import os
import re
from typing import Any, Dict, List

from flask import Blueprint, g, jsonify, request

from adk_backend.mail import (
    _get_configured_mail_channel, _get_mail_channel, _list_mail_channels,
)
from adk_backend.usage_scan import _dedupe_usage_entries
from adk_backend.utils import _coerce_int, advanced, project_deep_link

bp = Blueprint('email_tools', __name__)
_LOGGER = logging.getLogger(__name__)


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in ('1', 'true', 'yes', 'y', 'on')



def _usage_to_email_line(usage: Dict[str, Any]) -> str:
    object_type = usage.get('objectType') or usage.get('usageType') or 'OBJECT'
    object_name = usage.get('objectName') or usage.get('objectId') or 'unknown'
    project_key = usage.get('projectKey') or '?'
    code_env_name = usage.get('codeEnvName') or '?'
    return f"- [{object_type}] {object_name} (project={project_key}, code env={code_env_name})"


def _email_object_type_label(object_type: Any, usage_type: Any) -> str:
    raw = str(object_type or usage_type or 'OBJECT').strip().upper()
    if raw.startswith('RECIPE'):
        return 'Recipe'
    if raw.startswith('NOTEBOOK'):
        return 'Notebook'
    if raw.startswith('WEBAPP'):
        return 'Webapp Backend'
    if raw.startswith('SCENARIO_STEP'):
        return 'Scenario Step'
    if raw.startswith('SCENARIO'):
        return 'Scenario'
    if raw.startswith('CODE_STUDIO'):
        return 'Code Studio'
    if raw.startswith('PROJECT'):
        return 'Project'
    return raw.replace('_', ' ').title()


def _usage_lines_grouped_by_code_env(usages: List[Dict[str, Any]]) -> List[str]:
    grouped: Dict[str, List[str]] = {}
    seen = set()

    for usage in usages:
        if not isinstance(usage, dict):
            continue
        usage_type = str(usage.get('usageType') or '').strip().upper()
        if usage_type == 'PROJECT':
            # Project-level defaults are too generic for outreach emails.
            continue

        code_env = str(usage.get('codeEnvName') or usage.get('codeEnvKey') or 'Unknown').strip() or 'Unknown'
        project_key = str(usage.get('projectKey') or '?').strip() or '?'
        object_label = _email_object_type_label(usage.get('objectType'), usage_type)
        object_name = str(usage.get('objectName') or usage.get('objectId') or 'unknown').strip() or 'unknown'

        signature = (
            code_env.lower(),
            project_key,
            object_label.lower(),
            object_name,
        )
        if signature in seen:
            continue
        seen.add(signature)

        grouped.setdefault(code_env, []).append(
            f"- {object_label}: {object_name} (project={project_key})"
        )

    if not grouped:
        return ['- No concrete object usage details found']

    out: List[str] = []
    env_names = sorted(grouped.keys(), key=lambda name: name.lower())
    for idx, env_name in enumerate(env_names):
        out.append(f"Code Environment: {env_name}")
        env_lines = sorted(grouped[env_name], key=lambda line: line.lower())
        out.extend([f"  {line}" for line in env_lines])
        if idx < len(env_names) - 1:
            out.append('')
    return out


def _usage_lines_grouped_by_project(usages: List[Dict[str, Any]]) -> List[str]:
    grouped: Dict[str, Dict[str, List[str]]] = {}
    seen = set()

    for usage in usages:
        if not isinstance(usage, dict):
            continue
        usage_type = str(usage.get('usageType') or '').strip().upper()
        if usage_type == 'PROJECT':
            continue

        code_env = str(usage.get('codeEnvName') or usage.get('codeEnvKey') or 'Unknown').strip() or 'Unknown'
        project_key = str(usage.get('projectKey') or '?').strip() or '?'
        object_label = _email_object_type_label(usage.get('objectType'), usage_type)
        object_name = str(usage.get('objectName') or usage.get('objectId') or 'unknown').strip() or 'unknown'

        signature = (project_key, code_env.lower(), object_label.lower(), object_name)
        if signature in seen:
            continue
        seen.add(signature)

        grouped.setdefault(project_key, {}).setdefault(code_env, []).append(
            f"    - {object_label}: {object_name}"
        )

    if not grouped:
        return ['- No concrete object usage details found']

    out: List[str] = []
    project_keys = sorted(grouped.keys(), key=lambda k: k.lower())
    for idx, pkey in enumerate(project_keys):
        out.append(f"Project: {pkey}")
        envs = sorted(grouped[pkey].keys(), key=lambda e: e.lower())
        for env_name in envs:
            out.append(f"  - Code Env: {env_name}")
            obj_lines = sorted(grouped[pkey][env_name], key=lambda l: l.lower())
            out.extend(obj_lines)
        if idx < len(project_keys) - 1:
            out.append('')
    return out


def _wrap_html_email(body_html: str) -> str:
    year = __import__('datetime').datetime.now().year
    return (
        '<!-- html:true -->\n'
        '<html lang="en">\n'
        '<head>\n'
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width">\n'
        '    <meta http-equiv="X-UA-Compatible" content="IE=edge">\n'
        '    <title>DSS Health</title>\n'
        '    <style>\n'
        "        @import url('https://fonts.googleapis.com/css2?family=Source+Sans+3:ital,wght@0,200..900;1,200..900&display=swap');\n"
        '    </style>\n'
        '    <style type="text/css">\n'
        '        body, #bodyTable {\n'
        '            height: 100% !important; width: 100% !important;\n'
        '            margin: 0; padding: 0;\n'
        '            font-family: "Source Sans 3", -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;\n'
        '            background-color: #f4f5f7;\n'
        '        }\n'
        '        body, table, td, p, a, li, blockquote {\n'
        '            -ms-text-size-adjust: 100%; -webkit-text-size-adjust: 100%;\n'
        '        }\n'
        '        table { border-spacing: 0; }\n'
        '        table, td { border-collapse: collapse; mso-table-lspace: 0pt; mso-table-rspace: 0pt; }\n'
        '        img { -ms-interpolation-mode: bicubic; }\n'
        '        img, a img { border: 0; outline: none; text-decoration: none; }\n'
        '        .yshortcuts a { border-bottom: none !important; }\n'
        '        @media only screen and (min-width: 900px) {\n'
        '            .email-container { width: 880px !important; }\n'
        '        }\n'
        '        a { color: #00897b; }\n'
        '        .logo-header { text-align: left; margin-bottom: 16px; }\n'
        '        .logo { max-width: 120px; margin-bottom: 4px; }\n'
        '        .banner { width: 100%; max-width: 580px; margin: 4px auto 8px auto; display: block; }\n'
        '        .container {\n'
        '            background-color: #ffffff;\n'
        '            padding: 28px 36px 32px 36px;\n'
        '            border: 1px solid #e5eaf0;\n'
        '            border-radius: 12px;\n'
        '        }\n'
        '        .content {\n'
        '            color: #3a3f47;\n'
        '            font-size: 15px;\n'
        '            line-height: 1.6;\n'
        '        }\n'
        '        .content p { margin: 10px 0; color: #3a3f47; }\n'
        '        .content h3 { color: #1a1a2e; font-size: 16px; font-weight: 600; margin: 20px 0 8px 0; }\n'
        '        .content ul { padding-left: 20px; margin: 6px 0; line-height: 1.7; }\n'
        '        .content li { margin: 4px 0; color: #4a5568; }\n'
        '        .button {\n'
        '            display: inline-block; margin-top: 4px; margin-bottom: 12px;\n'
        '            padding: 12px 20px; text-decoration: none;\n'
        '            border-radius: 32px; font-weight: 500;\n'
        '        }\n'
        '        .btn-primary { background-color: #00897b; color: #ffffff; }\n'
        '        .btn-secondary { background-color: #ffffff; color: #00897b; border: 1px solid #00897b; }\n'
        '        .footer { text-align: center; color: #8895a7; font-size: 12px; padding: 32px 0; }\n'
        '    </style>\n'
        '</head>\n'
        '<table id="bodyTable" border="0" cellpadding="0" cellspacing="0" width="100%">\n'
        '    <tr>\n'
        '        <td align="center" valign="top">\n'
        '            <table align="center" border="0" cellpadding="0" cellspacing="0" class="email-container"\n'
        '                   style="max-width: 720px;">\n'
        '                <tr>\n'
        '                    <td height="20" style="font-size: 0; line-height: 0;">&nbsp;</td>\n'
        '                </tr>\n'
        '                <tr>\n'
        '                    <td>\n'
        '                        <div class="logo-header">\n'
        '                            <a href="https://www.dataiku.com">\n'
        '                                <img src="https://dku-assets.s3.amazonaws.com/img/emailing/DataikuLogoTeal_2025.png" alt="Dataiku Logo" class="logo">\n'
        '                            </a>\n'
        '                        </div>\n'
        '                    </td>\n'
        '                </tr>\n'
        '                <tr>\n'
        '                    <td>\n'
        '                        <div class="container">\n'
        '                            <div class="content">\n'
        '                                <img src="https://dku-assets.s3.amazonaws.com/img/emailing/EmailBanner.png" class="banner" alt="Banner">\n'
        + body_html +
        '\n                            </div>\n'
        '                        </div>\n'
        '                    </td>\n'
        '                </tr>\n'
        '                <tr>\n'
        '                    <td class="footer">\n'
        f'                        &copy; {year} Dataiku | All rights reserved.<br>\n'
        '                        <br>\n'
        '                        <a href="mailto:{{admin_email}}" class="button btn-primary" style="color:#ffffff;font-size:13px;padding:8px 18px;background-color:#00897b;text-decoration:none;border-radius:32px;display:inline-block;">Contact your DSS Admin</a>\n'
        '                        &nbsp;\n'
        '                        <a href="{{chat_channel_url}}" class="button btn-secondary" style="color:#00897b;font-size:13px;padding:8px 18px;background-color:#ffffff;text-decoration:none;border:1px solid #00897b;border-radius:32px;display:inline-block;">Join the DSS Channel</a>\n'
        '                    </td>\n'
        '                </tr>\n'
        '            </table>\n'
        '        </td>\n'
        '    </tr>\n'
        '</table>\n'
        '</html>\n'
    )


def _text_body_to_html(rendered_text: str) -> str:
    import html as _html
    lines = rendered_text.split('\n')
    fragments: List[str] = []
    in_list = False
    in_sub_list = False

    _p_style = 'style="margin:10px 0;color:#3a3f47;font-size:15px;line-height:1.6;"'
    _h3_style = 'style="color:#1a1a2e;font-size:15px;font-weight:600;margin:20px 0 6px 0;padding:0;"'
    _ul_style = 'style="padding-left:20px;margin:6px 0;"'
    _li_style = 'style="margin:4px 0;color:#3a3f47;font-size:14px;line-height:1.5;"'
    _li_sub_style = 'style="margin:3px 0;color:#4a5568;font-size:13px;line-height:1.5;"'

    def _close_sub_list():
        nonlocal in_sub_list
        if in_sub_list:
            fragments.append('</ul></li>')
            in_sub_list = False

    def _close_list():
        nonlocal in_list
        _close_sub_list()
        if in_list:
            fragments.append('</ul>')
            in_list = False

    for line in lines:
        stripped = line.rstrip()

        # Section headers
        if stripped.startswith('Project:') or stripped.startswith('Code Environment:'):
            _close_list()
            fragments.append(f'<h3 {_h3_style}>' + _html.escape(stripped) + '</h3>')
            continue

        # Deeply indented list item (4+ spaces then "- ")
        if stripped.startswith('    - ') or stripped.startswith('\t\t- '):
            content = stripped.lstrip().lstrip('- ').strip()
            if not in_list:
                fragments.append(f'<ul {_ul_style}>')
                in_list = True
            if not in_sub_list:
                fragments.append(f'<li {_li_style}><ul {_ul_style}>')
                in_sub_list = True
            fragments.append(f'<li {_li_sub_style}>' + _html.escape(content) + '</li>')
            continue

        # Indented list item (2 spaces then "- ")
        if stripped.startswith('  - ') or stripped.startswith('\t- '):
            _close_sub_list()
            content = stripped.lstrip().lstrip('- ').strip()
            if not in_list:
                fragments.append(f'<ul {_ul_style}>')
                in_list = True
            fragments.append(f'<li {_li_style}>' + _html.escape(content) + '</li>')
            continue

        # Top-level list item ("- ")
        if stripped.startswith('- '):
            _close_sub_list()
            content = stripped[2:].strip()
            if not in_list:
                fragments.append(f'<ul {_ul_style}>')
                in_list = True
            fragments.append(f'<li {_li_style}>' + _html.escape(content) + '</li>')
            continue

        # Empty line = paragraph break
        if not stripped:
            _close_list()
            continue

        # Regular text line
        _close_list()
        fragments.append(f'<p {_p_style}>' + _html.escape(stripped) + '</p>')

    _close_list()
    return _wrap_html_email('\n'.join(fragments))


_PROJECT_ENV_MARKER = '__PEL_HTML__'


def _build_project_env_html(projects_data: list, _pel_grouped: dict) -> str:
    """Build rich HTML cards for the project -> code env -> objects hierarchy."""
    import html as _html
    cards: List[str] = []

    for proj in projects_data:
        if not isinstance(proj, dict):
            continue
        pkey = str(proj.get('projectKey') or '')
        pname = str(proj.get('name') or pkey)
        ce_count = _coerce_int(proj.get('codeEnvCount'), 0)

        parts: List[str] = []
        parts.append(
            '<table cellpadding="0" cellspacing="0" width="100%" style="'
            'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
            'margin:14px 0;font-family:inherit;">'
        )

        # ── Header row ──
        name_html = _html.escape(pname)
        if pname != pkey and pkey:
            name_html += (
                f' <span style="color:#8895a7;font-weight:400;font-size:13px;">'
                f'({_html.escape(pkey)})</span>'
            )
        badge = ''
        if ce_count:
            badge = (
                f' <span style="display:inline-block;background:#e0f2f1;color:#00897b;'
                f'font-size:11px;font-weight:600;padding:2px 10px;border-radius:10px;'
                f'margin-left:6px;vertical-align:middle;letter-spacing:0.3px;">'
                f'{ce_count} code env{"s" if ce_count != 1 else ""}</span>'
            )
        parts.append(
            f'<tr><td style="padding:14px 20px 10px 20px;font-weight:600;font-size:15px;'
            f'color:#1a1a2e;border-bottom:1px solid #eef0f4;">'
            f'{name_html}{badge}</td></tr>'
        )

        # ── Code env entries ──
        env_data = _pel_grouped.get(pkey, {})
        env_names = sorted(env_data.keys(), key=lambda e: e.lower()) if env_data else []
        if not env_names:
            env_names = sorted(set(
                str(n) for n in (proj.get('codeEnvNames') or []) if str(n).strip()
            ))

        for idx, env_name in enumerate(env_names):
            obj_lines = env_data.get(env_name, []) if env_data else []
            is_last = idx == len(env_names) - 1

            inner = (
                f'<div style="margin:0 0 2px 0;">'
                f'<span style="display:inline-block;color:#00897b;font-weight:600;'
                f'font-size:13px;">&#9679;&nbsp; {_html.escape(env_name)}</span></div>'
            )

            if obj_lines:
                tags = []
                for obj_line in sorted(obj_lines, key=lambda l: l.lower()):
                    obj_stripped = obj_line.strip()
                    if ':' in obj_stripped:
                        obj_type, obj_name = obj_stripped.split(':', 1)
                        tags.append(
                            f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                            f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                            f'line-height:1.4;">'
                            f'<span style="color:#8895a7;font-weight:500;">'
                            f'{_html.escape(obj_type.strip())}</span>'
                            f' {_html.escape(obj_name.strip())}</span>'
                        )
                    else:
                        tags.append(
                            f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                            f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                            f'line-height:1.4;">{_html.escape(obj_stripped)}</span>'
                        )
                inner += f'<div style="margin:4px 0 0 18px;">{"".join(tags)}</div>'

            bottom_pad = '12px' if is_last else '6px'
            sep = '' if is_last else 'border-bottom:1px solid #f2f4f6;'
            parts.append(
                f'<tr><td style="padding:10px 20px {bottom_pad} 20px;{sep}">'
                f'{inner}</td></tr>'
            )

        parts.append('</table>')
        cards.append('\n'.join(parts))

    if not cards:
        return (
            '<p style="color:#8895a7;font-size:14px;font-style:italic;">'
            'No code environment details available.</p>'
        )
    return '\n'.join(cards)


# ── Markers for rich-HTML injection (all email list variables) ──
_PROJECT_LIST_MARKER = '__PLIST_HTML__'
_CODE_ENV_LIST_MARKER = '__CELIST_HTML__'
_OBJECTS_LIST_MARKER = '__OLIST_HTML__'
_CODE_STUDIO_LIST_MARKER = '__CSLIST_HTML__'
_SCENARIO_LIST_MARKER = '__SCLIST_HTML__'
_INACTIVE_LIST_MARKER = '__IPLIST_HTML__'


def _build_items_html(items: List[str], accent: str = '#3a3f47', links: Dict[str, str] = None) -> str:
    """Render a flat list of items as styled inline tags; items with a link
    become clickable (project deep links)."""
    import html as _html
    if not items:
        return '<span style="color:#8895a7;font-size:13px;font-style:italic;">none</span>'
    tags = []
    for item in items:
        label = _html.escape(item)
        href = (links or {}).get(item)
        if href:
            label = (f'<a href="{_html.escape(href, quote=True)}" '
                     f'style="color:{accent};text-decoration:underline;">{label}</a>')
        tags.append(
            f'<span style="display:inline-block;background:#f0f2f5;color:{accent};'
            f'font-size:13px;font-weight:500;padding:5px 14px;border-radius:6px;'
            f'margin:3px 4px 3px 0;line-height:1.4;">{label}</span>'
        )
    return f'<div style="margin:8px 0 4px 0;">{"".join(tags)}</div>'


def _build_code_studio_html(projects_data: list) -> str:
    """Render code studio counts per project as a styled card."""
    import html as _html
    rows: List[str] = []
    valid = [p for p in projects_data if isinstance(p, dict)]
    for idx, proj in enumerate(valid):
        pkey = str(proj.get('projectKey') or '')
        pname = str(proj.get('name') or pkey)
        cs_count = _coerce_int(proj.get('codeStudioCount'), 0)
        is_last = idx == len(valid) - 1
        sep = '' if is_last else 'border-bottom:1px solid #f2f4f6;'

        name_html = _html.escape(pname)
        if pname != pkey and pkey:
            name_html += (
                f' <span style="color:#8895a7;font-weight:400;font-size:13px;">'
                f'({_html.escape(pkey)})</span>'
            )
        badge = (
            f' <span style="display:inline-block;background:#fff3e0;color:#e65100;'
            f'font-size:11px;font-weight:600;padding:2px 10px;border-radius:10px;'
            f'margin-left:6px;vertical-align:middle;">'
            f'{cs_count} code studio{"s" if cs_count != 1 else ""}</span>'
        )
        rows.append(
            f'<tr><td style="padding:12px 20px;{sep}font-weight:600;font-size:14px;color:#1a1a2e;">'
            f'{name_html}{badge}</td></tr>'
        )
    if not rows:
        return '<span style="color:#8895a7;font-size:13px;font-style:italic;">none</span>'
    return (
        '<table cellpadding="0" cellspacing="0" width="100%" style="'
        'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
        'margin:14px 0;font-family:inherit;">'
        + ''.join(rows) + '</table>'
    )


def _build_scenario_html(projects_data: list) -> str:
    """Render scenario details per project as styled cards."""
    import html as _html
    cards: List[str] = []
    for proj in projects_data:
        if not isinstance(proj, dict):
            continue
        auto_scenarios = proj.get('autoScenarios') or []
        if not auto_scenarios:
            continue
        pkey = str(proj.get('projectKey') or '')
        pname = str(proj.get('name') or pkey)

        parts: List[str] = []
        parts.append(
            '<table cellpadding="0" cellspacing="0" width="100%" style="'
            'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
            'margin:14px 0;font-family:inherit;">'
        )

        # Header
        name_html = _html.escape(pname)
        if pname != pkey and pkey:
            name_html += (
                f' <span style="color:#8895a7;font-weight:400;font-size:13px;">'
                f'({_html.escape(pkey)})</span>'
            )
        valid_sc = [s for s in auto_scenarios if isinstance(s, dict)]
        badge = (
            f' <span style="display:inline-block;background:#e8eaf6;color:#3949ab;'
            f'font-size:11px;font-weight:600;padding:2px 10px;border-radius:10px;'
            f'margin-left:6px;vertical-align:middle;">'
            f'{len(valid_sc)} scenario{"s" if len(valid_sc) != 1 else ""}</span>'
        )
        parts.append(
            f'<tr><td style="padding:14px 20px 10px 20px;font-weight:600;font-size:15px;'
            f'color:#1a1a2e;border-bottom:1px solid #eef0f4;">'
            f'{name_html}{badge}</td></tr>'
        )

        # Scenario rows
        for sidx, sc in enumerate(valid_sc):
            sc_name = str(sc.get('name') or sc.get('id') or 'Unknown')
            sc_type = str(sc.get('type') or 'unknown')
            trigger_count = _coerce_int(sc.get('triggerCount'), 0)
            is_last = sidx == len(valid_sc) - 1

            inner = (
                f'<div style="margin:0 0 2px 0;">'
                f'<span style="display:inline-block;color:#3949ab;font-weight:600;'
                f'font-size:13px;">&#9679;&nbsp; {_html.escape(sc_name)}</span></div>'
            )
            meta_tags = (
                f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                f'line-height:1.4;">'
                f'<span style="color:#8895a7;font-weight:500;">type</span>'
                f' {_html.escape(sc_type)}</span>'
                f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                f'line-height:1.4;">'
                f'<span style="color:#8895a7;font-weight:500;">triggers</span>'
                f' {trigger_count}</span>'
            )
            inner += f'<div style="margin:4px 0 0 18px;">{meta_tags}</div>'

            bottom_pad = '12px' if is_last else '6px'
            sep = '' if is_last else 'border-bottom:1px solid #f2f4f6;'
            parts.append(
                f'<tr><td style="padding:10px 20px {bottom_pad} 20px;{sep}">'
                f'{inner}</td></tr>'
            )

        parts.append('</table>')
        cards.append('\n'.join(parts))
    if not cards:
        return '<span style="color:#8895a7;font-size:13px;font-style:italic;">none</span>'
    return '\n'.join(cards)


def _build_inactive_projects_html(projects_data: list) -> str:
    """Render inactive projects as a styled card with duration badges."""
    import html as _html
    rows: List[str] = []
    valid = [p for p in projects_data if isinstance(p, dict)]
    for idx, proj in enumerate(valid):
        pkey = str(proj.get('projectKey') or '')
        pname = str(proj.get('name') or pkey)
        days_inactive = _coerce_int(proj.get('daysInactive'), 0)
        is_last = idx == len(valid) - 1
        sep = '' if is_last else 'border-bottom:1px solid #f2f4f6;'

        name_html = _html.escape(pname)
        if pname != pkey and pkey:
            name_html += (
                f' <span style="color:#8895a7;font-weight:400;font-size:13px;">'
                f'({_html.escape(pkey)})</span>'
            )
        badge = ''
        if days_inactive > 0:
            badge = (
                f' <span style="display:inline-block;background:#fff3e0;color:#e65100;'
                f'font-size:11px;font-weight:600;padding:2px 10px;border-radius:10px;'
                f'margin-left:6px;vertical-align:middle;">'
                f'inactive {days_inactive} days</span>'
            )
        rows.append(
            f'<tr><td style="padding:12px 20px;{sep}font-weight:600;font-size:14px;color:#1a1a2e;">'
            f'{name_html}{badge}</td></tr>'
        )
    if not rows:
        return '<span style="color:#8895a7;font-size:13px;font-style:italic;">none</span>'
    return (
        '<table cellpadding="0" cellspacing="0" width="100%" style="'
        'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
        'margin:14px 0;font-family:inherit;">'
        + ''.join(rows) + '</table>'
    )


def _build_objects_html(usage_details: list, group_by_project: bool = False) -> str:
    """Render usage objects as styled cards, grouped by code env or project."""
    import html as _html

    if group_by_project:
        # Group by project → code env → objects
        grouped: Dict[str, Dict[str, List[tuple]]] = {}
        seen: set = set()
        for u in usage_details:
            if not isinstance(u, dict):
                continue
            usage_type = str(u.get('usageType') or '').strip().upper()
            if usage_type == 'PROJECT':
                continue
            ce = str(u.get('codeEnvName') or u.get('codeEnvKey') or 'Unknown').strip() or 'Unknown'
            pk = str(u.get('projectKey') or '?').strip() or '?'
            obj_label = _email_object_type_label(u.get('objectType'), usage_type)
            obj_name = str(u.get('objectName') or u.get('objectId') or 'unknown').strip() or 'unknown'
            sig = (pk, ce.lower(), obj_label.lower(), obj_name)
            if sig in seen:
                continue
            seen.add(sig)
            grouped.setdefault(pk, {}).setdefault(ce, []).append((obj_label, obj_name))

        if not grouped:
            return '<span style="color:#8895a7;font-size:13px;font-style:italic;">No object usage details found</span>'

        cards: List[str] = []
        for pkey in sorted(grouped.keys(), key=lambda k: k.lower()):
            parts: List[str] = []
            parts.append(
                '<table cellpadding="0" cellspacing="0" width="100%" style="'
                'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
                'margin:14px 0;font-family:inherit;">'
            )
            parts.append(
                f'<tr><td style="padding:14px 20px 10px 20px;font-weight:600;font-size:15px;'
                f'color:#1a1a2e;border-bottom:1px solid #eef0f4;">'
                f'{_html.escape(pkey)}</td></tr>'
            )
            envs = sorted(grouped[pkey].keys(), key=lambda e: e.lower())
            for eidx, env_name in enumerate(envs):
                objs = grouped[pkey][env_name]
                is_last = eidx == len(envs) - 1
                inner = (
                    f'<div style="margin:0 0 2px 0;">'
                    f'<span style="display:inline-block;color:#00897b;font-weight:600;'
                    f'font-size:13px;">&#9679;&nbsp; {_html.escape(env_name)}</span></div>'
                )
                if objs:
                    tags = []
                    for obj_label, obj_name in sorted(objs, key=lambda x: x[1].lower()):
                        tags.append(
                            f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                            f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                            f'line-height:1.4;">'
                            f'<span style="color:#8895a7;font-weight:500;">'
                            f'{_html.escape(obj_label)}</span>'
                            f' {_html.escape(obj_name)}</span>'
                        )
                    inner += f'<div style="margin:4px 0 0 18px;">{"".join(tags)}</div>'
                bottom_pad = '12px' if is_last else '6px'
                sep = '' if is_last else 'border-bottom:1px solid #f2f4f6;'
                parts.append(
                    f'<tr><td style="padding:10px 20px {bottom_pad} 20px;{sep}">'
                    f'{inner}</td></tr>'
                )
            parts.append('</table>')
            cards.append('\n'.join(parts))
        return '\n'.join(cards)

    # Group by code env → objects (with project context)
    grouped_by_env: Dict[str, List[tuple]] = {}
    seen2: set = set()
    for u in usage_details:
        if not isinstance(u, dict):
            continue
        usage_type = str(u.get('usageType') or '').strip().upper()
        if usage_type == 'PROJECT':
            continue
        ce = str(u.get('codeEnvName') or u.get('codeEnvKey') or 'Unknown').strip() or 'Unknown'
        pk = str(u.get('projectKey') or '?').strip() or '?'
        obj_label = _email_object_type_label(u.get('objectType'), usage_type)
        obj_name = str(u.get('objectName') or u.get('objectId') or 'unknown').strip() or 'unknown'
        sig = (ce.lower(), pk, obj_label.lower(), obj_name)
        if sig in seen2:
            continue
        seen2.add(sig)
        grouped_by_env.setdefault(ce, []).append((obj_label, obj_name, pk))

    if not grouped_by_env:
        return '<span style="color:#8895a7;font-size:13px;font-style:italic;">No object usage details found</span>'

    cards2: List[str] = []
    for env_name in sorted(grouped_by_env.keys(), key=lambda n: n.lower()):
        objs = grouped_by_env[env_name]
        parts2: List[str] = []
        parts2.append(
            '<table cellpadding="0" cellspacing="0" width="100%" style="'
            'background:#fafbfc;border:1px solid #e5eaf0;border-radius:8px;'
            'margin:14px 0;font-family:inherit;">'
        )
        parts2.append(
            f'<tr><td style="padding:14px 20px 10px 20px;font-weight:600;font-size:15px;'
            f'color:#00897b;border-bottom:1px solid #eef0f4;">'
            f'&#9679;&nbsp; {_html.escape(env_name)}</td></tr>'
        )
        tags = []
        for obj_label, obj_name, pk in sorted(objs, key=lambda x: (x[2].lower(), x[1].lower())):
            tags.append(
                f'<span style="display:inline-block;background:#eef0f5;color:#4a5568;'
                f'font-size:12px;padding:3px 10px;border-radius:4px;margin:2px 3px 2px 0;'
                f'line-height:1.4;">'
                f'<span style="color:#8895a7;font-weight:500;">'
                f'{_html.escape(obj_label)}</span>'
                f' {_html.escape(obj_name)}'
                f' <span style="color:#b0b8c4;font-size:11px;">({_html.escape(pk)})</span>'
                f'</span>'
            )
        parts2.append(
            f'<tr><td style="padding:10px 20px 12px 20px;">'
            f'<div style="margin:4px 0 0 0;">{"".join(tags)}</div>'
            f'</td></tr>'
        )
        parts2.append('</table>')
        cards2.append('\n'.join(parts2))
    return '\n'.join(cards2)


def _default_email_template(campaign: str) -> Dict[str, str]:
    if campaign == 'compute_local':
        return {
            'subject': '[DSS Health] Workloads running on the DSS host instead of containers',
            'body': (
                "Hi {{owner}},\n\n"
                "DSS health checks found recipes, webapps, ML tasks or notebooks in your projects that run "
                "on the DSS server itself (local compute) rather than on a containerized execution config.\n"
                "Local workloads compete for the server's CPU and memory and can slow down every user. "
                "Please switch them to a containerized execution config (project Settings → Container "
                "execution, or the object's Advanced tab).\n\n"
                "Impacted projects:\n{{project_list}}\n\n"
                "Objects still on local compute:\n{{objects_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'code_env':
        return {
            'subject': '[DSS Health] Code environment ownership mismatch in your projects',
            'body': (
                "Hi {{owner}},\n\n"
                "DSS health checks flagged code environments in your projects that are owned by other users.\n"
                "Project owners should own their project code environments (ideally one per project) so changes do not break other projects.\n\n"
                "Impacted projects:\n{{project_list}}\n\n"
                "Code environments not owned by you:\n{{code_env_list}}\n\n"
                "Detected objects:\n{{objects_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'code_studio':
        return {
            'subject': '[DSS Health] Too many Code Studios in your projects',
            'body': (
                "Hi {{owner}},\n\n"
                "DSS health checks flagged that some of your projects have too many Code Studios.\n"
                "Please consolidate or remove unused Code Studios to reduce resource consumption.\n\n"
                "Projects with excessive Code Studios:\n{{code_studio_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'auto_scenario':
        return {
            'subject': '[DSS Health] Review auto-start scenarios in your projects',
            'body': (
                "Hi {{owner}},\n\n"
                "DSS health checks found scenarios set to automatically start in your projects.\n"
                "Please review these scenarios to ensure they are still needed and properly configured.\n\n"
                "Projects and auto-start scenarios:\n{{scenario_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'disabled_user':
        return {
            'subject': '[DSS Health] Projects owned by disabled users need reassignment',
            'body': (
                "Hi admin,\n\n"
                "The following projects are owned by disabled user accounts.\n"
                "Please reassign ownership to active users.\n\n"
                "Projects owned by disabled users:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'deprecated_code_env':
        return {
            'subject': '[DSS Health] Deprecated Python versions in your code environments',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your code environments use deprecated Python versions (2.x, 3.6, or 3.7).\n"
                "Please upgrade to a supported Python version.\n\n"
                "Code environments:\n{{code_env_list}}\n\n"
                "Impacted projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'default_code_env':
        return {
            'subject': '[DSS Health] Projects missing default code environment',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects use code environments but have no default Python code environment configured.\n"
                "Setting a default code environment prevents unexpected version conflicts.\n\n"
                "Projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'overshared_project':
        return {
            'subject': '[DSS Health] Projects with excessive permissions',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects have a large number of permission entries.\n"
                "Please review and consolidate permissions using groups where possible.\n\n"
                "Projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'scenario_frequency':
        return {
            'subject': '[DSS Health] High-frequency scenarios in your projects',
            'body': (
                "Hi {{owner}},\n\n"
                "Some scenarios in your projects run very frequently (under 30 minutes).\n"
                "Please review whether this frequency is necessary.\n\n"
                "Projects and scenarios:\n{{scenario_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'empty_project':
        return {
            'subject': '[DSS Health] Empty projects that may need cleanup',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects appear to be empty or unused.\n"
                "Please archive or delete projects that are no longer needed.\n\n"
                "Projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'large_flow':
        return {
            'subject': '[DSS Health] Projects with large flows',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects have very large flows with many objects.\n"
                "Consider splitting large flows into smaller, focused projects.\n\n"
                "Projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'orphan_notebooks':
        return {
            'subject': '[DSS Health] Projects with many notebooks but few recipes',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects have many notebooks but few recipes.\n"
                "Consider converting mature notebooks into recipes for production use.\n\n"
                "Projects:\n{{project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'scenario_failing':
        return {
            'subject': '[DSS Health] Failing scenarios in your projects',
            'body': (
                "Hi {{owner}},\n\n"
                "Some scenarios in your projects have failed in their last run.\n"
                "Please investigate and fix the failing scenarios.\n\n"
                "Projects and failing scenarios:\n{{scenario_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'inactive_project':
        return {
            'subject': '[DSS Health] Inactive projects that may need cleanup',
            'body': (
                "Hi {{owner}},\n\n"
                "Some of your projects have been inactive for a long time.\n"
                "A project is considered inactive when it has no recent modifications, "
                "no active scenarios, and no deployed bundles.\n\n"
                "Please delete or archive projects that are no longer needed to keep the instance clean.\n\n"
                "Inactive projects:\n{{inactive_project_list}}\n\n"
                "Thanks."
            ),
        }
    if campaign == 'unused_code_env':
        return {
            'subject': '[DSS Health] Unused code environments you own',
            'body': (
                "Hi {{owner}},\n\n"
                "Some code environments you own have zero usages across all projects.\n"
                "Please delete code environments that are no longer needed to free up resources.\n\n"
                "Unused code environments:\n{{code_env_list}}\n\n"
                "Thanks."
            ),
        }
    return {
        'subject': '[DSS Health] Please reduce code environments in your projects',
        'body': (
            "Hi {{owner}},\n\n"
            "DSS health checks flagged that some of your projects use too many code environments.\n"
            "Please keep one code environment per project unless absolutely necessary.\n\n"
            "{{project_env_list}}\n\n"
            "Thanks."
        ),
    }


def _render_template_text(template: str, variables: Dict[str, str]) -> str:
    out = template or ''
    for key, value in variables.items():
        out = out.replace(f'{{{{{key}}}}}', value)
    return out


@bp.route('/api/tools/email/preview', methods=['POST'])
def api_tools_email_preview():
    payload = request.get_json(silent=True) or {}
    _valid_campaigns = {
        'project', 'code_env', 'code_studio', 'auto_scenario',
        'disabled_user', 'deprecated_code_env', 'default_code_env',
        'overshared_project', 'scenario_frequency', 'empty_project',
        'large_flow', 'orphan_notebooks', 'scenario_failing',
        'inactive_project', 'unused_code_env', 'compute_local',
    }
    campaign = str(payload.get('campaign') or 'project').strip().lower()
    if campaign not in _valid_campaigns:
        campaign = 'project'

    template_payload = payload.get('template') if isinstance(payload.get('template'), dict) else {}
    defaults = _default_email_template(campaign)
    subject_template = str(template_payload.get('subject') or defaults['subject'])
    body_template = str(template_payload.get('body') or defaults['body'])
    recipients = payload.get('recipients')
    if not isinstance(recipients, list):
        recipients = []

    previews: List[Dict[str, Any]] = []
    for recipient in recipients:
        if not isinstance(recipient, dict):
            continue

        owner = str(recipient.get('owner') or recipient.get('recipientKey') or 'Unknown')
        to_email = str(recipient.get('email') or owner).strip()
        project_keys = sorted({str(key) for key in (recipient.get('projectKeys') or []) if str(key).strip()})
        code_env_names = sorted({str(name) for name in (recipient.get('codeEnvNames') or []) if str(name).strip()})
        usage_details = [
            usage for usage in (recipient.get('usageDetails') or [])
            if isinstance(usage, dict)
        ]
        usage_details = _dedupe_usage_entries(usage_details)
        if campaign in ('project', 'compute_local'):
            object_lines = _usage_lines_grouped_by_project(usage_details)
        else:
            object_lines = _usage_lines_grouped_by_code_env(usage_details)

        # Project deep links (studioExternalUrl-based): plain key lists give
        # recipients nothing to click — link every project we name.
        project_links = {key: project_deep_link(g.client, key) for key in project_keys}
        project_links = {k: v for k, v in project_links.items() if v}

        def _project_line(key: str) -> str:
            link = project_links.get(key)
            return f"- {key} — {link}" if link else f"- {key}"

        variables = {
            'owner': owner,
            'owner_email': to_email,
            'project_count': str(len(project_keys)),
            'code_env_count': str(len(code_env_names)),
            'object_count': str(len(usage_details)),
            'project_list': '\n'.join([_project_line(key) for key in project_keys]) if project_keys else '- none',
            'code_env_list': '\n'.join([f"- {name}" for name in code_env_names]) if code_env_names else '- none',
            'objects_list': '\n'.join(object_lines),
            'project_keys': ', '.join(project_keys) if project_keys else 'none',
            'code_envs': ', '.join(code_env_names) if code_env_names else 'none',
        }

        projects_data = recipient.get('projects') or []
        code_studio_lines = []
        for proj in projects_data:
            if not isinstance(proj, dict):
                continue
            pname = str(proj.get('name') or proj.get('projectKey') or 'Unknown')
            pkey = str(proj.get('projectKey') or '')
            cs_count = _coerce_int(proj.get('codeStudioCount'), 0)
            code_studio_lines.append(f"- {pname} ({pkey}): {cs_count} code studios")
        variables['code_studio_list'] = '\n'.join(code_studio_lines) if code_studio_lines else '- none'

        scenario_lines = []
        for proj in projects_data:
            if not isinstance(proj, dict):
                continue
            auto_scenarios = proj.get('autoScenarios') or []
            if not auto_scenarios:
                continue
            pname = str(proj.get('name') or proj.get('projectKey') or 'Unknown')
            pkey = str(proj.get('projectKey') or '')
            scenario_lines.append(f"Project: {pname} ({pkey})")
            for sc in auto_scenarios:
                if not isinstance(sc, dict):
                    continue
                sc_name = str(sc.get('name') or sc.get('id') or 'Unknown')
                sc_type = str(sc.get('type') or 'unknown')
                trigger_count = _coerce_int(sc.get('triggerCount'), 0)
                scenario_lines.append(f"  - {sc_name} (type={sc_type}, triggers={trigger_count})")
        variables['scenario_list'] = '\n'.join(scenario_lines) if scenario_lines else '- none'

        inactive_project_lines = []
        for proj in projects_data:
            if not isinstance(proj, dict):
                continue
            pname = str(proj.get('name') or proj.get('projectKey') or 'Unknown')
            pkey = str(proj.get('projectKey') or '')
            days_inactive = _coerce_int(proj.get('daysInactive'), 0)
            link = project_deep_link(g.client, pkey)
            link_suffix = f" — {link}" if link else ''
            if days_inactive > 0:
                inactive_project_lines.append(
                    f"- {pname} ({pkey}): inactive for {days_inactive} days{link_suffix}")
            else:
                inactive_project_lines.append(f"- {pname} ({pkey}){link_suffix}")
        variables['inactive_project_list'] = '\n'.join(inactive_project_lines) if inactive_project_lines else '- none'

        # Build project_env_list: project → code envs → objects (where used)
        # Group usage_details by projectKey → codeEnvName → object lines
        _pel_grouped: Dict[str, Dict[str, List[str]]] = {}
        _pel_seen: set = set()
        for u in usage_details:
            if not isinstance(u, dict):
                continue
            pk = str(u.get('projectKey') or '').strip()
            ce = str(u.get('codeEnvName') or u.get('codeEnvKey') or '').strip()
            if not pk or not ce:
                continue
            usage_type = str(u.get('usageType') or '').strip().upper()
            _pel_grouped.setdefault(pk, {}).setdefault(ce, [])
            # Skip PROJECT-level defaults for object lines (they have no real object)
            if usage_type == 'PROJECT':
                continue
            obj_label = _email_object_type_label(u.get('objectType'), usage_type)
            obj_name = str(u.get('objectName') or u.get('objectId') or '').strip()
            if obj_name:
                sig = (pk, ce.lower(), obj_label.lower(), obj_name)
                if sig not in _pel_seen:
                    _pel_seen.add(sig)
                    _pel_grouped[pk][ce].append(f"      {obj_label}: {obj_name}")

        project_env_lines: List[str] = []
        for proj in projects_data:
            if not isinstance(proj, dict):
                continue
            pkey = str(proj.get('projectKey') or '')
            pname = str(proj.get('name') or pkey)
            ce_count = _coerce_int(proj.get('codeEnvCount'), 0)
            header = pname if pname == pkey else f"{pname} ({pkey})"
            if ce_count:
                header += f" — {ce_count} code envs"
            project_env_lines.append(header)
            env_data = _pel_grouped.get(pkey, {})
            if env_data:
                for env_name in sorted(env_data.keys(), key=lambda e: e.lower()):
                    project_env_lines.append(f"  - {env_name}")
                    for obj_line in sorted(env_data[env_name], key=lambda l: l.lower()):
                        project_env_lines.append(obj_line)
            else:
                # Fallback: use per-project code env names (from projects array)
                proj_env_names = sorted(set(str(n) for n in (proj.get('codeEnvNames') or []) if str(n).strip()))
                for name in proj_env_names:
                    project_env_lines.append(f"  - {name}")
        variables['project_env_list'] = '\n'.join(project_env_lines) if project_env_lines else '- none'

        # Build rich HTML for all list variables
        _rich_html_map = {
            'project_env_list': (_PROJECT_ENV_MARKER, _build_project_env_html(projects_data, _pel_grouped)),
            'project_list': (_PROJECT_LIST_MARKER, _build_items_html(project_keys, links=project_links)),
            'code_env_list': (_CODE_ENV_LIST_MARKER, _build_items_html(code_env_names, accent='#00897b')),
            'objects_list': (_OBJECTS_LIST_MARKER, _build_objects_html(usage_details, group_by_project=(campaign in ('project', 'compute_local')))),
            'code_studio_list': (_CODE_STUDIO_LIST_MARKER, _build_code_studio_html(projects_data)),
            'scenario_list': (_SCENARIO_LIST_MARKER, _build_scenario_html(projects_data)),
            'inactive_project_list': (_INACTIVE_LIST_MARKER, _build_inactive_projects_html(projects_data)),
        }

        _preview_debug = {
            'usageDetailsCount': len(usage_details),
            'usageTypes': sorted({str(u.get('usageType') or '') for u in usage_details}),
            'envGroups': {k: list(v.keys()) for k, v in _pel_grouped.items()},
            'projectsInRecipient': [
                {'projectKey': proj.get('projectKey'), 'codeEnvNames': proj.get('codeEnvNames')}
                for proj in projects_data if isinstance(proj, dict)
            ],
        }
        _LOGGER.info("[tools] email-preview campaign=%s owner=%s debug=%s", campaign, owner, _preview_debug)

        # Swap list variables with markers for rich HTML injection
        for _var_name, (_marker, _html_val) in _rich_html_map.items():
            if '{{' + _var_name + '}}' in body_template:
                variables[_var_name] = _marker

        rendered_body_text = _render_template_text(body_template, variables)
        body_html = _text_body_to_html(rendered_body_text)

        # Inject rich HTML for all list variables
        for _var_name, (_marker, _html_val) in _rich_html_map.items():
            if _marker in body_html:
                body_html = body_html.replace(_marker, _html_val)
        # Replace footer placeholders in the final HTML wrapper
        admin_email = str(payload.get('adminEmail') or 'dss-admin@your-company.com').strip()
        chat_channel_url = str(payload.get('chatChannelUrl') or '#').strip()
        body_html = body_html.replace('{{admin_email}}', admin_email)
        body_html = body_html.replace('{{chat_channel_url}}', chat_channel_url)
        preview = {
            'recipientKey': str(recipient.get('recipientKey') or owner),
            'owner': owner,
            'to': to_email,
            'projectKeys': project_keys,
            'codeEnvNames': code_env_names,
            'projectKeyForSend': recipient.get('projectKeyForSend') or (project_keys[0] if project_keys else None) or os.environ.get('DKU_CURRENT_PROJECT_KEY', ''),
            'objectCount': len(usage_details),
            'subject': _render_template_text(subject_template, variables),
            'body': body_html,
            'usageDetails': usage_details,
            '_debug': _preview_debug,
        }

        previews.append(preview)

    _LOGGER.info("[tools] preview campaign=%s recipients=%s", campaign, len(previews))
    return jsonify({
        'campaign': campaign,
        'template': {
            'subject': subject_template,
            'body': body_template,
        },
        'previews': previews,
        'count': len(previews),
    })


@bp.route('/api/tools/email/send', methods=['POST'])
@advanced
def api_tools_email_send():
    client = g.client
    payload = request.get_json(silent=True) or {}
    campaign = str(payload.get('campaign') or 'project').strip().lower()

    requested_channel = str(payload.get('channelId') or '').strip() or None
    plain_text = _parse_bool(payload.get('plainText'), True)

    previews = payload.get('previews')
    if not isinstance(previews, list):
        previews = []

    channels = _list_mail_channels(client)
    if not channels:
        _LOGGER.warning("[tools] send failed: no DSS mail channel configured")
        return jsonify({'error': 'No DSS mail channel configured'}), 400

    # Priority: request payload > plugin param > first available
    effective_channel = requested_channel or _get_configured_mail_channel() or None
    selected = channels[0]
    if effective_channel:
        for channel in channels:
            if channel.get('id') == effective_channel:
                selected = channel
                break
    selected_id = str(selected.get('id') or '')

    channel_obj = _get_mail_channel(client, selected_id)
    if channel_obj is None:
        _LOGGER.warning("[tools] send failed: cannot resolve mail channel %s", selected_id)
        return jsonify({'error': f'Unable to load mail channel: {selected_id}'}), 400

    results: List[Dict[str, Any]] = []
    sent_count = 0
    for preview in previews:
        if not isinstance(preview, dict):
            continue
        recipient_key = str(preview.get('recipientKey') or '')
        to_email = str(preview.get('to') or '').strip()
        project_key = str(preview.get('projectKeyForSend') or '').strip()
        if not project_key:
            project_key = os.environ.get('DKU_CURRENT_PROJECT_KEY', '')
        subject = str(preview.get('subject') or '').strip()
        body = str(preview.get('body') or '')

        to_email = re.sub(r'[\r\n]', '', to_email)
        subject = re.sub(r'[\r\n]', '', subject)
        if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', to_email):
            results.append({
                'recipientKey': recipient_key,
                'to': to_email,
                'projectKeyForSend': project_key,
                'status': 'error',
                'error': 'Invalid email address format',
            })
            continue

        if not to_email or not project_key or not subject:
            results.append({
                'recipientKey': recipient_key,
                'to': to_email,
                'projectKeyForSend': project_key,
                'status': 'error',
                'error': 'Missing to/projectKeyForSend/subject',
            })
            continue

        try:
            channel_obj.send(project_key, [to_email], subject, body, plain_text=plain_text)
            sent_count += 1
            results.append({
                'recipientKey': recipient_key,
                'to': to_email,
                'projectKeyForSend': project_key,
                'status': 'sent',
            })
        except Exception as exc:
            _LOGGER.warning("[tools] send failed recipient=%s to=%s: %s", recipient_key, to_email, exc)
            results.append({
                'recipientKey': recipient_key,
                'to': to_email,
                'projectKeyForSend': project_key,
                'status': 'error',
                'error': str(exc),
            })

    _LOGGER.info(
        "[tools] send campaign=%s channel=%s requested=%s sent=%s total=%s",
        campaign,
        selected_id,
        len(previews),
        sent_count,
        len(results),
    )
    return jsonify({
        'campaign': campaign,
        'channelId': selected_id,
        'requestedCount': len(previews),
        'sentCount': sent_count,
        'results': results,
    })
