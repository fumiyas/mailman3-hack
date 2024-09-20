## -*- coding: utf-8 -*- vim:shiftwidth=4:expandtab:
##
## mailman shell: Display mailing-lists configurations in JSON/JSONL
##
## SPDX-FileCopyrightText: 2024 SATOH Fumiyasu @ OSSTech Corp., Japan
## SPDX-License-Identifier: GPL-3.0-or-later
##

import sys
import re
import enum
import datetime
import fnmatch
import json
import click

from mailman.interfaces.listmanager import IListManager
from sqlalchemy.ext.mutable import MutableList
from types import GeneratorType
from zope.component import getUtility


KEYS_EXCLUDED = {
    "bounces_address",
    "data_path",
    "list_id",
    ## NetNews
    "gateway_to_mail",
    "gateway_to_news",
    "linked_newsgroup",
    "newsgroup_moderation",
    "nntp_prefix_subject_too",
}


@click.command(
    help="""Display mailing-lists configurations in JSON/JSONL"""
)
@click.option(
    "--keys", "keys_str",
    help="""Oputput only specified key(s) and value(s)."""
)
@click.argument(
    'list_patterns',
    metavar="LISTPATTERN",
    required=False,
    nargs=-1,
)
def cli(keys_str, list_patterns):
    keys = keys_str.split(",") if keys_str else ()

    out_opts = {"ensure_ascii": False}
    if sys.stdout.isatty():
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

        for key in dir(mlist):
            if key.startswith("_") or key.endswith("_address"):
                continue
            elif keys and key not in keys:
                continue
            elif key in KEYS_EXCLUDED:
                continue

            value = getattr(mlist, key)
            if isinstance(value, (bool, int, float, str)):
                pass
            elif isinstance(value, (GeneratorType, MutableList)):
                value = [v for v in value]
            elif isinstance(value, enum.Enum):
                value = str(value)
            elif isinstance(value, datetime.timedelta):
                continue
                ## FIXME
                #value = str(value)
                #...
            else:
                continue

            conf[key] = value

        print(json.dumps(conf, **out_opts))


def conf_lists(*argv):
    sys.argv = ["mailman shell --run conf_lists --", *argv]
    cli()
