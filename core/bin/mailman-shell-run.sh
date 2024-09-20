#!/bin/sh
##
## Mailman: Run `mailman shell` script under the specified or the same directory
##
## SPDX-FileCopyrightText: 2024 SATOH Fumiyasu @ OSSTech Corp., Japan
## SPDX-License-Identifier: GPL-3.0-or-later
##

set -u

ex_usage=64  # EX_USAGE: command line usage error

perr() {
  echo "$0: ERROR: $1" 1>&2
}

pdie() {
  perr "$1"
  exit "${2-1}"
}

if [ $# -lt 1 ]; then
  echo "Usage: $0 <SCRIPT_FILENAME> [MAILMAN SHELL OPTIONS/ARGUMENTS ...]"
  exit "$ex_usage"
fi

script="${1%.py}"; shift

if [ "${script##*/*}" != "$script" ]; then
  pythonpath="${script%/*}"
  script="${script##*/}"
else
  pythonpath="${0%/*}"
fi

script_path="$pythonpath/$script.py"
if [ ! -f "$script_path" ]; then
  pdie "Script not found: $script_path" "$ex_usage"
fi

export PYTHONPATH="$pythonpath${PYTHONPATH:+:$PYTHONPATH}"
exec mailman shell --run "$script" "$@"
