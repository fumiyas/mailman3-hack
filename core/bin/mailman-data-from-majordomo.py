#!/usr/bin/env python3.12
# -*- coding: utf-8 -*- vim:shiftwidth=4:expandtab:
#
# Mailman 3: Generate lists configuration data from Majordomo lists data
#
# NOTE: For OSSTech Mailman 3: https://gitlab.osstech.co.jp/product/mailman
#
# SPDX-FileCopyrightText: 2025 SATOH Fumiyasu @ OSSTech Corp., Japan
# SPDX-License-Identifier: GPL-3.0-or-later
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "click",
# ]
# ///

import contextlib
import logging
import os
import sys
import re
import json

import click

logger = logging.getLogger(__name__)

ALIAS_RE = re.compile(
    r"^(?P<name>\w[-\w]+):\s*(?P<values>.*)$",
    re.ASCII | re.IGNORECASE
)

MJ_OUTGOING_RE = re.compile(
    r"^:include:/\S+/(?P<list_name>\w[-\w]+)$",
    re.ASCII
)

MJ_SEQUENCER_ARGS_RE = re.compile(
    r"^\|\s*/\S+/wrapper "
    r"sequencer -l (?P<list_name>\w[-\w]+) "
    r"-n "
    r"-h (?P<domain_name>\w[-.\w]+) -N (?P<list_outgoing_name>\w[-\w]+)$",
    re.ASCII,
)

MJ_REQUEST_ARGS_RE = re.compile(
    r"^\|\s*/\S+/wrapper " r"request-answer (?P<list_name>\w[-\w]+)$", re.ASCII
)


class MajoromoListAliasEntryHasMultipleRecipientsError(Exception):
    pass


class MajoromoListPostNameMismatchError(Exception):
    pass


class MajoromoListOutgoingFileNameMismatchError(Exception):
    pass


class MajoromoListOutgoingAliasNameMismatchError(Exception):
    pass


class MajoromoUnknownListDomainError(Exception):
    pass


class MajoromoInvalidOutgoingAliasRecipientError(Exception):
    pass


class MajoromoNoOutgoingAliasEntryForListError(Exception):
    pass


class MajoromoInvalidRequestCommandError(Exception):
    pass


class MajoromoListRequestNameMismatchError(Exception):
    pass


class MajoromoInvalidListConfigLineError(Exception):
    pass


class MajordomoConfigUnicodeDecodeError(Exception):
    pass


class MajordomoInvalidConfigValueError(Exception):
    pass


def file2aliases(aliases_file):
    f = open(aliases_file)

    aliases = {}
    for line in f:
        ## FIXME: A line that starts with whitespace continues a logical line.
        line = re.sub(r"\s+", " ", line.strip())
        if line == "" or line.startswith("#"):
            continue

        m = ALIAS_RE.search(line)

        if m["name"] in aliases:
            logger.warning(f"Duplicate alias entry: {line}")

        aliases[m["name"]] = {
            "line": line,
            "recipients": [
                re.sub(r'^(")?(.*)\1$', r"\2", v.strip())
                for v in m["values"].split(",")
            ],
        }

    return aliases


def mj_config_read(config_file):
    #if os.path.getsize(config_file) == 0:
    #    raise ValueError(f"Empty Majordomo config file: {config_file}")

    f = open(config_file, "rb")

    config = {}
    line_no = 0
    while line := f.readline():
        line_no += 1
        if line.startswith(b"#"):
            continue

        try:
            line = line.decode("UTF-8")
        except UnicodeDecodeError as e:
            raise MajordomoConfigUnicodeDecodeError(f"{config_file}: line {line_no}: {line!r}") from e

        line = line.strip()
        if line == "" or line.startswith("#"):
            continue

        if m := re.search(r"^(?P<key>\w+)\s*=\s*(?P<value>.*)$", line, re.ASCII):
            config[m["key"]] = m["value"].strip()
        elif m := re.search(r"^(?P<key>\w+)\s*<<\s*(?P<mark>\w+)\s*$", line, re.ASCII):
            key = m["key"]
            mark = m["mark"]
            value = ""
            while line := f.readline():
                line_no += 1
                try:
                    line = line.decode("UTF-8")
                except UnicodeDecodeError as e:
                    raise MajordomoConfigUnicodeDecodeError(
                        f"{config_file}: {key} << {mark}: line {line_no}: {line!r}"
                    ) from e
                if line.rstrip() == mark:
                    break
                value += line
            continue
        else:
            raise MajoromoInvalidListConfigLineError(f"{config_file}: line {line_no}: {line!r}")

    ## FIXME: Support ` `- or `:`-separated file names
    #config["restrict_post"] = [
    #    x
    #    for x in re.split(r"[ :]", config["restrict_post"])
    #    if len(x) > 0
    #]

    return config


@click.command(
    help="""Generate OSSTech Mailman 3 lists data from Majordomo lists"""
)
@click.option(
    "--majordomo-target-lists-file",
    "mj_target_lists_file",
    metavar="FILE",
    help="""
        File contains target Majordomo lists names.
    """,
)
@click.option(
    "--majordomo-domain-name",
    "mj_domain_name",
    metavar="DOMAIN",
    help="""
        Majordomo lists domain name.
    """,
)
@click.option(
    "--majordomo-outgoing-name",
    "mj_outgoing_name",
    default="outgoing",
    metavar="OUTGOING",
    help="""
        Extra name for Majordomo lists `<listname>-<OUTGOING>` alias entry.
    """,
    show_default=True,
)
@click.option(
    "--majordomo-extra-names",
    "mj_extra_names_csv",
    multiple=True,
    metavar="EXTRA",
    help="""
        Extra name(s) for Majordomo lists `<listname>-<EXTRA>` and
        `owner-<listname>-<EXTRA>` alias entries to ignore.
    """,
)
@click.option(
    "--default-owner",
    "mm_owner_default",
    metavar="EMAIL",
    help="""
        Default list owner address if Majordomo list has no owner address.
    """,
)
@click.option(
    "--exclude-list-name", "-x",
    "list_name_excluded_csv",
    multiple=True,
    metavar="NAME",
    help="""
        Exclude specified list name(s).
    """,
)
@click.option(
    "--ignore-no-majordomo-config-lists",
    is_flag=True,
    default=False,
    help="""
        Majordomo lists in aliases that has no <listname>.config file in
        the Majordomo data directory.
    """,
)
@click.argument(
    'mj_aliases_file',
    required=True,
)
@click.argument(
    'mj_lists_dir',
    required=True,
)
@click.argument(
    'mm_domain_name',
    required=True,
)
def main(
    mj_aliases_file, mj_lists_dir, mj_domain_name,
    mm_domain_name, mm_owner_default,
    mj_target_lists_file, mj_outgoing_name, mj_extra_names_csv,
    list_name_excluded_csv,
    ignore_no_majordomo_config_lists,
):
    if not mj_domain_name:
        mj_domain_name = mm_domain_name

    mj_target_list_names = None
    if mj_target_lists_file:
        with open(mj_target_lists_file) as f:
            mj_target_list_names = [
                ## Remove trailing `,...` for CSV (`<listname>,<extras>...`)
                re.sub(r",.*", "", line).strip()
                for line in f.readlines()
            ]

    mj_extra_names = set(
        y
        ## Split each items and flatten
        for x in mj_extra_names_csv
        for y in x.split(",")
    )
    list_name_excluded = set(
        y
        ## Split each items and flatten
        for x in list_name_excluded_csv
        for y in x.split(",")
    )

    mj_aliases = file2aliases(mj_aliases_file)
    mj_list_names = set(
        x.removesuffix(".config")
        for x in os.listdir(mj_lists_dir)
        if x.endswith(".config")
    )

    ## Majordomo リスト名 → メンバーリストエイリアス名の一覧
    mj_outgoing_name_by_name = {}
    for list_name, mj_list_alias in mj_aliases.items():
        m = MJ_SEQUENCER_ARGS_RE.search(mj_list_alias["recipients"][0])
        if not m:
            continue

        if len(mj_list_alias["recipients"]) > 1:
            raise MajoromoListAliasEntryHasMultipleRecipientsError(mj_list_alias["line"])
        if m["list_name"] != list_name:
            raise MajoromoListPostNameMismatchError(mj_list_alias["line"])
        if m["list_outgoing_name"] != f"{list_name}-{mj_outgoing_name}":
            raise MajoromoListOutgoingAliasNameMismatchError(mj_list_alias["line"])
        if m["domain_name"] != mj_domain_name:
            raise MajoromoUnknownListDomainError(mj_list_alias["line"])

        mj_outgoing_name_by_name[list_name] = m["list_outgoing_name"]
        try:
            mj_list_names.remove(list_name)
        except KeyError:
            pass

    for mj_list_name in mj_list_names:
        logger.warning(f"No alias entry in aliases for Majordomo list: {mj_list_name}")

    ## Majordomo リスト → Mailman 3 リスト
    for list_name, mj_outgoing_name in mj_outgoing_name_by_name.items():
        del mj_aliases[list_name]

        if mj_request_a := mj_aliases.pop(f"{list_name}-request", None):
            mj_request_a
            if len(mj_request_a["recipients"]) > 1:
                raise MajoromoListAliasEntryHasMultipleRecipientsError(
                    mj_request_a["line"]
                )
            m = MJ_REQUEST_ARGS_RE.search(mj_request_a["recipients"][0])
            if not m:
                raise MajoromoInvalidRequestCommandError(mj_request_a["line"])
            if m["list_name"] != list_name:
                raise MajoromoListRequestNameMismatchError(mj_request_a["line"])
        mj_aliases.pop(f"{list_name}-approval", None)
        mj_aliases.pop(f"owner-{list_name}-request", None)
        for mj_extra_name in mj_extra_names:
            mj_aliases.pop(f"{list_name}-{mj_extra_name}", None)
            mj_aliases.pop(f"owner-{list_name}-{mj_extra_name}", None)

        mj_outgoing_alias = mj_aliases.pop(mj_outgoing_name, None)
        mj_owners_alias = mj_aliases.pop(f"owner-{list_name}", None)

        if list_name in list_name_excluded:
            continue
        if mj_target_list_names and list_name not in mj_target_list_names:
            continue

        if mj_outgoing_alias is None:
            raise MajoromoNoOutgoingAliasEntryForListError(f"No {mj_outgoing_name} entry in {mj_aliases_file}")

        ## Parse `<listname>-outgoing` alias entry
        if len(mj_outgoing_alias["recipients"]) > 1:
            raise MajoromoListAliasEntryHasMultipleRecipientsError(mj_outgoing_alias["line"])
        m = MJ_OUTGOING_RE.search(mj_outgoing_alias["recipients"][0])
        if not m:
            raise MajoromoInvalidOutgoingAliasRecipientError(mj_outgoing_alias["line"])
        if m["list_name"] != list_name:
            raise MajoromoListOutgoingFileNameMismatchError(mj_outgoing_alias["line"])

        mj_config_file = f"{mj_lists_dir}/{list_name}.config"
        try:
            mj_config = mj_config_read(mj_config_file)
        except FileNotFoundError:
            if ignore_no_majordomo_config_lists:
                logging.warning(f"Unable to read list config file: {mj_config_file} (ignored)")
                continue
            logging.error(f"Unable to read list config file: {mj_config_file}")
            raise
        except Exception:
            logging.error(f"Unable to read list config file: {mj_config_file}")
            raise

        mm_config = {
            "fqdn_listname": f"{list_name}@{mm_domain_name}",
            "admin_immed_notify": True,
            "admin_notify_mchanges": mj_config.get("announcements", "yes") == "yes",
            "description": mj_config.get("description", ""),
            ## FIXME: "info": Read `<listname>.info` file contents
            "send_welcome_message": mj_config.get("welcome", "yes") == "yes",
            "administrivia": mj_config.get("administrivia", "yes") == "yes",
            "max_message_size": int(mj_config.get("maxlength", 40000)) // 1000,
            ## FIXME: Remove header handler: "purge_received"
            ## FIXME: Add to header filter: "taboo_headers"
            ## FIXME: XXX: "taboo_body" (unable to support?)
            ## FIXME: Add header handler: "message_header"
            ## FIXME: Message header file: "message_fronter"
            ## FIXME: Message footer file:" "message_footer"
        }

        if mj_config.get("moderate", "no") == "yes":
            mm_config["default_member_action"] = "Action.hold"
            mm_config["default_nonmember_action"] = "Action.hold"
        else:
            mm_config["default_member_action"] = "Action.defer"
            ## FIXME: Support ` `- or `:`-separated file names
            match mj_config.get("restrict_post", ""):
                case "":
                    mm_config["default_nonmember_action"] = "Action.defer"
                case x if x == list_name:
                    mm_config["default_nonmember_action"] = "Action.reject"
                case x:
                    ## FIXME: Read mj_config("restrict_post") files and add to mm_config["accept_these_nonmembers"]
                    raise MajordomoInvalidConfigValueError(f"{mj_config_file}: restrict_post={x}")

        match mj_config.get("who_access", "open"):
            case "open":
                mm_config["member_roster_visibility"] = "RosterVisibility.public"
            case "list":
                mm_config["member_roster_visibility"] = "RosterVisibility.members"
            case "closed":
                mm_config["member_roster_visibility"] = "RosterVisibility.moderators"
            case x:
                raise MajordomoInvalidConfigValueError(f"{mj_config_file}: who_access={x}")

        match mj_config.get("subscribe_policy", "open+confirm"):
            case "open" | "auto":
                mm_config["subscription_policy"] = "SubscriptionPolicy.open"
            case "open+confirm" | "auto+confirm":
                mm_config["subscription_policy"] = "SubscriptionPolicy.confirm"
            case "closed":
                mm_config["subscription_policy"] = "SubscriptionPolicy.moderate"
            case x:
                raise MajordomoInvalidConfigValueError(f"{mj_config_file}: subscribe_policy={x}")

        match mj_config.get("unsubscribe_policy", "open"):
            case "open" | "auto":
                mm_config["unsubscription_policy"] = "SubscriptionPolicy.open"
            case "open+confirm" | "auto+confirm":
                mm_config["unsubscription_policy"] = "SubscriptionPolicy.confirm"
            case "closed":
                mm_config["unsubscription_policy"] = "SubscriptionPolicy.moderate"
            case x:
                raise MajordomoInvalidConfigValueError(f"{mj_config_file}: unsubscribe_policy={x}")

        mm_config["subject_prefix"] = (
            mj_config.get("subject_prefix", "")
            .replace("$LIST", list_name)
            .replace("$SEQNUM", "%d")
        )

        if reply_to := mj_config.get("reply_to", ""):
            mm_config["first_strip_reply_to"] = True
            if reply_to == f"{list_name}@{mj_domain_name}" or reply_to == list_name:
                mm_config["reply_goes_to_list"] = "ReplyToMunging.point_to_list"
                mm_config["reply_to_address"] = reply_to
            else:
                mm_config["reply_goes_to_list"] = "ReplyToMunging.explicit_header_only"
                mm_config["reply_to_address"] = ""
        else:
            mm_config["first_strip_reply_to"] = False
            mm_config["reply_goes_to_list"] = "ReplyToMunging.no_munging"
            mm_config["reply_to_address"] = ""

        match mj_config.get("index_access", "open"):
            case "open":
                mm_config["archive_policy"] = "ArchivePolicy.public"
            case "list":
                mm_config["archive_policy"] = "ArchivePolicy.private"
            case x:
                raise MajordomoInvalidConfigValueError(f"{mj_config_file}: index_access={x}")

        mj_seq_file = f"{mj_lists_dir}/{list_name}.seq"
        mj_seq_str = open(mj_seq_file).read().strip()
        try:
            mm_config["post_id"] = int(mj_seq_str)
        except Exception as e:
            raise MajordomoInvalidConfigValueError(f"{mj_seq_file}: {mj_seq_str!r}") from e

        mm_config_file = f"{mj_lists_dir}/{list_name}.mm.config.jsonl"
        mm_owners_file = f"{mj_lists_dir}/{list_name}.mm.owners.txt"
        ## FIXME: Write mj_config.get("moderator", "").split(",") to mm_moderator_file
        #mm_moderators_file = f"{mj_lists_dir}/{list_name}.mm.moderators.txt"
        mm_members_file = f"{mj_lists_dir}/{list_name}.mm.members.txt"
        with contextlib.suppress(FileNotFoundError):
            os.remove(mm_config_file)
            os.remove(mm_owners_file)
            os.remove(mm_members_file)

        with open(mm_config_file, 'w') as f:
            print(json.dumps(mm_config), file=f)

        with open(mm_owners_file, 'w') as f:
            ## FIXME: Validate owners email addresses
            if mj_owners_alias:
                print(*mj_owners_alias["recipients"], sep="\n", file=f)
            else:
                if mm_owner_default:
                    print(mm_owner_default)

        mj_outgoing_file = f"{mj_lists_dir}/{list_name}"
        mm_members = [
            line.strip()
            for line in open(mj_outgoing_file).read().split('\n')
            if not line.startswith('#') and line.strip()
        ]
        with open(mm_members_file, 'w') as f:
            print(*mm_members, sep="\n", file=f)

    for name, entry in mj_aliases.items():
        logger.warning(f"Dangling alias entry: {entry['line']}")

    return 0


if __name__ == "__main__":
    logging_handlers = [logging.StreamHandler()]
    logging.basicConfig(
        level=logging.WARN,
        handlers=logging_handlers,
        format=(f"{sys.argv[0]}: %(levelname)s: %(message)s"),
    )

    sys.exit(main(sys.argv[1:]))
