## -*- coding: utf-8 -*- vim:shiftwidth=4:expandtab:
##
## mailman shell: Modify mailing-lists configurations based on JSONL input
##
## SPDX-FileCopyrightText: 2024-2026 SATOH Fumiyasu @ OSSTech Corp., Japan
## SPDX-License-Identifier: GPL-3.0-or-later
##

import sys
import re
import json
import click

from datetime import datetime, timedelta, UTC
from mailman.database.transaction import transaction
from mailman.interfaces.action import Action, FilterAction
from mailman.interfaces.archiver import ArchivePolicy
from mailman.interfaces.autorespond import ResponseAction
from mailman.interfaces.bounce import UnrecognizedBounceDisposition
from mailman.interfaces.digests import DigestFrequency
from mailman.interfaces.languages import ILanguage
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.mailinglist import (
    ArchiveRenderingMode,
    DMARCMitigateAction,
    IAcceptableAliasSet,
    IHeaderMatchList,
    IListArchiverSet,
    Personalization,
    ReplyToMunging,
    SubscriptionPolicy,
)
from mailman.interfaces.nntp import NewsgroupModeration
from mailman.model.roster import RosterVisibility
from sqlalchemy.ext.mutable import MutableList
from types import GeneratorType
from zope.component import getUtility


def map_archive_rendering_mode(value):
    return ArchiveRenderingMode[value.removeprefix("ArchiveRenderingMode.")]


def map_archive_policy(value):
    return ArchivePolicy[value.removeprefix("ArchivePolicy.")]


def map_autoresponse_action(value):
    return ResponseAction[value.removeprefix("ResponseAction.")]


def map_default_posting_action(value):
    return Action[value.removeprefix("Action.")]


def map_digest_frequency(value):
    return DigestFrequency[value.removeprefix("DigestFrequency.")]


def map_dmarc_mitigate_action(value):
    return DMARCMitigateAction[value.removeprefix("DMARCMitigateAction.")]


def map_filter_action(value):
    return FilterAction[value.removeprefix("FilterAction.")]


def map_newsgroup_moderation(value):
    return NewsgroupModeration[value.removeprefix("NewsgroupModeration.")]


def map_personalization(value):
    return Personalization[value.removeprefix("Personalization.")]


def map_reply_to_munging(value):
    return ReplyToMunging[value.removeprefix("ReplyToMunging.")]


def map_roster_visibility(value):
    return RosterVisibility[value.removeprefix("RosterVisibility.")]


def map_subscription_policy(value):
    return SubscriptionPolicy[value.removeprefix("SubscriptionPolicy.")]


def map_unrecognized_bounce_disposition(value):
    return UnrecognizedBounceDisposition[value.removeprefix("UnrecognizedBounceDisposition.")]


KEYS_EXCLUDED = {
    "id",
    "list_id",
    "data_path",
    ## Sub addresses
    "bounces_address",
    "join_address",
    "leave_address",
    "no_reply_address",
    "owner_address",
    "posting_address",
    "request_address",
    "subscribe_address",
    "unsubscribe_address",
}

VALUE_MAPPINGS = dict(
    archive_rendering_mode=map_archive_rendering_mode,
    archive_policy=map_archive_policy,
    autorespond_owner=map_autoresponse_action,
    autorespond_postings=map_autoresponse_action,
    autorespond_requests=map_autoresponse_action,
    default_member_action=map_default_posting_action,
    default_nonmember_action=map_default_posting_action,
    digest_volume_frequency=map_digest_frequency,
    dmarc_mitigate_action=map_dmarc_mitigate_action,
    filter_action=map_filter_action,
    forward_unrecognized_bounces_to=map_unrecognized_bounce_disposition,
    member_roster_visibility=map_roster_visibility,
    newsgroup_moderation=map_newsgroup_moderation,
    personalize=map_personalization,
    reply_goes_to_list=map_reply_to_munging,
    subscription_policy=map_subscription_policy,
    unsubscription_policy=map_subscription_policy,
)


@click.command(
    help="""\
    Modify mailing-lists configurations based on JSONL input and show old
    configurations in JSON fromat.

    Example JSONL (JSON Lines):

    \b
    {"fqdn_listname": "list1@example.jp", "post_id": 12, "advertised": false}
    {"fqdn_listname": "list2@example.jp", "default_nonmember_action": "defer"}
    """
)
@click.option(
    "--output-format",
    "-f",
    type=click.Choice(("jsonl", "cjson")),
    default="jsonl",
    show_default=True,
    help="""
        Specify the output format.

        \b
        jsonl: JSON Lines (Newline-delimited JSON)
        cjson: Concatenated JSON
        """,
)
def cli(output_format):
    out_opts = {"ensure_ascii": False}
    if output_format == "cjson":
        out_opts["indent"] = 2

    list_manager = getUtility(IListManager)
    for line_no, line in enumerate(sys.stdin, start=1):
        try:
            conf = json.loads(line)
        except json.decoder.JSONDecodeError:
            print(f"ERROR: Invalid JSONL input: line {line_no}: {line!r}", file=sys.stderr)
            sys.exit(1)

        fqdn_listname = conf.pop("fqdn_listname")
        conf_old = {"fqdn_listname": fqdn_listname}

        mlist = list_manager.get(fqdn_listname)
        if mlist is None:
            print(f"ERROR: No such list: {fqdn_listname}", file=sys.stderr)
            sys.exit(1)

        with transaction():
            for key, value in conf.items():
                if key in KEYS_EXCLUDED:
                    continue

                if key == "acceptable_aliases":
                    conf_old[key] = [v.alias for v in mlist.acceptablealias]
                    alias_set = IAcceptableAliasSet(mlist)
                    alias_set.clear()
                    for alias_new in value:
                        alias_set.add(alias_new)
                    continue
                elif key == "archivers":
                    conf_old[key] = {
                        v.name: v.is_enabled
                        for v in mlist.listarchiver
                        if v.name in value
                    }
                    archiver_set = IListArchiverSet(mlist)
                    for archiver_name, is_enabled in value.items():
                        if is_enabled is None:
                            if archiver_name in conf_old[key]:
                                archiver_set.remove(archiver_name)
                        else:
                            # If the archiver is not enabled, this is ignored.
                            archiver_set.get(archiver_name).is_enabled = is_enabled
                    continue
                elif key == "header_matches":
                    conf_old[key] = [
                        {
                            "header": v.header,
                            "pattern": v.pattern,
                            "chain": v.chain,
                        }
                        for v in mlist.header_matches
                    ]
                    header_matches = IHeaderMatchList(mlist)
                    header_matches.clear()
                    for matches_new in value:
                        header_matches.append(
                            matches_new["header"],
                            matches_new["pattern"],
                            matches_new["chain"],
                        )
                    continue

                if not hasattr(mlist, key):
                    raise KeyError(
                        f"{fqdn_listname}: Unknown key: {key}={value!r}"
                    )

                value_old = getattr(mlist, key)
                if key in VALUE_MAPPINGS:
                    try:
                        value = VALUE_MAPPINGS[key](value)
                    except KeyError:
                        raise ValueError(
                            f"{fqdn_listname}: Invalid value: {key}={value!r}"
                        )
                    value_old = str(value_old)
                elif isinstance(value_old, type(value)):
                    pass
                elif isinstance(value_old, datetime):
                    ## Must be UTC to apply to database
                    value = datetime.fromisoformat(value).astimezone(UTC)
                    ## UTC w/o tzinfo -> UTC -> local time -> ISO 8601
                    value_old = value_old.replace(tzinfo=UTC).astimezone().isoformat()
                elif isinstance(value_old, timedelta):
                    m = re.match(
                        r"((?P<days>\d+)\s*d(ays?)?)?,?"
                        r"(\s*(?P<hours>\d{1,2})(:(?P<minutes>\d{1,2})(:(?P<seconds>\d{1,2}))?)?)?$",
                        value.strip(),
                        re.ASCII
                    )
                    if not m:
                        raise ValueError(
                            f"{fqdn_listname}: Invalid timedelta string: {key}={value!r}"
                        )
                    value = timedelta(
                        days=int(m.group("days") or 0),
                        hours=int(m.group("hours") or 0),
                        seconds=int(m.group("seconds") or 0),
                        minutes=int(m.group("minutes") or 0),
                    )
                    value_old = str(value_old)
                elif ILanguage.providedBy(value_old):
                    value_old = value_old.code
                elif isinstance(value, list) and isinstance(value_old, (GeneratorType, MutableList)):
                    value_old = [v for v in value_old]
                else:
                    raise ValueError(
                        f"{fqdn_listname}: Invalid type of value: {key}={value!r}"
                        f" ({type(value)}, but {type(value_old)} expected)"
                    )

                conf_old[key] = value_old
                setattr(mlist, key, value)

        print(json.dumps(conf_old, **out_opts))


def lists_conf_modify(*argv):
    sys.argv = ["mailman shell --run lists_conf_modify --", *argv]
    cli()
