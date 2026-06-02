from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError
from typing_extensions import override

from analytics.lib.counts import do_drop_single_stat
from analytics.management import get_count_stat
from zerver.lib.management import ZulipBaseCommand


class Command(ZulipBaseCommand):
    help = """Clear analytics tables."""

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("--force", action="store_true", help="Actually do it.")
        parser.add_argument("--property", help="The property of the stat to be cleared.")

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        stat_property = options["property"]
        get_count_stat(stat_property)
        if not options["force"]:
            raise CommandError("No action taken. Use --force.")

        do_drop_single_stat(stat_property)
