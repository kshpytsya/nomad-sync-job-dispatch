import logging
import sys
import typing as tp

import click
import click_log


logger = logging.getLogger(__name__)
click_log.basic_config(logger)


def main() -> None:
    try:
        root(standalone_mode=False)
    except click.ClickException as e:
        logger.error(e.format_message())
        sys.exit()
    except click.exceptions.Abort:
        logger.error("aborted")
        sys.exit()


@click.group()
@click.version_option()
@click_log.simple_verbosity_option(logger, show_default=True)  # type: ignore
def root(**opts: tp.Any) -> None:
    """
    """
