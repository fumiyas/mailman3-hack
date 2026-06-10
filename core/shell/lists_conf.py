## -*- coding: utf-8 -*- vim:shiftwidth=4:expandtab:
##
## mailman shell: Display mailing-lists configurations in JSON format
##
## SPDX-FileCopyrightText: 2024-2026 SATOH Fumiyasu @ OSSTech Corp., Japan
## SPDX-License-Identifier: GPL-3.0-or-later
##

import sys
import re
import enum
import datetime
import fnmatch
import json
import click

from mailman.interfaces.languages import ILanguage
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.mailinglist import (
    IAcceptableAliasSet,
    IHeaderMatchList,
    IListArchiverSet,
)
from sqlalchemy.ext.mutable import MutableList
from types import GeneratorType
from zope.component import getUtility


KEYS_EXCLUDED = {
    "data_path",
    "list_id",
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
    ## Digests (OSSTech Mailman 3 does not support it)
    "digest_is_default",
    "digest_last_sent_at",
    "digest_send_periodic",
    "digest_size_threshold",
    "digest_volume_frequency",
    "digests_enabled",
    ## NetNews (OSSTech Mailman 3 does not support it)
    "gateway_to_mail",
    "gateway_to_news",
    "linked_newsgroup",
    "newsgroup_moderation",
    "nntp_prefix_subject_too",
}


class NoSuchAttr():
    pass


@click.command(
    help="""Display mailing-lists configurations in JSON format."""
)
@click.option(
    "--keys", "keys_str",
    help="""Oputput only specified key(s) and value(s)."""
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
@click.argument(
    'list_patterns',
    metavar="LISTPATTERN",
    required=False,
    nargs=-1,
)
def cli(keys_str, output_format, list_patterns):
    if keys_str:
        keys = set(keys_str.split(","))
    else:
        keys = None

    out_opts = {"ensure_ascii": False}
    if output_format == "cjson":
        out_opts["indent"] = 2

    list_res = [
        re.compile(p if p[0] == "^" else fnmatch.translate(p), re.IGNORECASE)
        for p in list_patterns
    ]

    list_manager = getUtility(IListManager)
    for mlist in list_manager.mailing_lists:
        fqdn_listname = mlist.fqdn_listname
        if list_res:
            for list_re in list_res:
                if list_re.match(fqdn_listname):
                    break
            else:
                continue

        conf = {"fqdn_listname": fqdn_listname}

        if keys is None:
            keys = [
                "acceptable_aliases",
                "archivers",
                "header_matches",
            ]
            keys += [
                key
                for key in dir(mlist)
                if (
                    key not in KEYS_EXCLUDED
                    and not key.startswith("_")
                    and not key.endswith("_password")
                )
            ]
            keys.sort()

        for key in keys:
            value = getattr(mlist, key, NoSuchAttr)
            if key == "acceptable_aliases":
                value = [v for v in IAcceptableAliasSet(mlist).aliases]
            elif key == "archivers":
                value = {v.name: v.is_enabled for v in IListArchiverSet(mlist).archivers}
            elif key == "header_matches":
                value = [
                    {
                        "header": v.header,
                        "pattern": v.pattern,
                        "chain": v.chain,
                    }
                    for v in IHeaderMatchList(mlist)
                ]
            elif isinstance(value, (bool, int, float, str)):
                pass
            elif isinstance(value, bytes):
                value = value.decode("UTF-8")
            elif isinstance(value, (GeneratorType, MutableList)):
                value = list(value)
            elif isinstance(value, (enum.Enum, datetime.timedelta)):
                value = str(value)
            elif isinstance(value, datetime.datetime):
                ## UTC w/o tzinfo -> UTC -> local time -> ISO 8601
                value = value.replace(tzinfo=datetime.UTC).astimezone().isoformat()
            elif ILanguage.providedBy(value):
                value = value.code
            elif value is NoSuchAttr:
                print(f"No such key: {key}", file=sys.stderr)
                continue
            else:
                continue

            conf[key] = value

        print(json.dumps(conf, **out_opts))


def lists_conf(*argv):
    sys.argv = ["mailman shell --run lists_conf --", *argv]
    cli()
