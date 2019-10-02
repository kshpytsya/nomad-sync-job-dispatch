import base64
import logging
import sys
import typing as tp

import click
import click_log
import nomad


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


def validate_meta(
    ctx: tp.Any,
    param: tp.Any,
    values: tp.Tuple[str, ...],
) -> tp.Dict[str, str]:
    result = {}

    for value in values:
        fields = value.split("=", 1)
        if len(fields) != 2:
            raise click.BadParameter("must be in form of \"key=value\"")

        result[fields[0]] = fields[1]

    return result


# note: help strings that have direct counterparts in "nomad job dispatch"
# are copied verbatim from there.

@click.command()
@click.version_option()
@click_log.simple_verbosity_option(logger, show_default=True)  # type: ignore
@click.option(
    "--address",
    metavar="<addr>",
    help="The address of the Nomad server. "
    + "Overrides the NOMAD_ADDR environment variable if set.",
)
@click.option(
    "--region",
    metavar="<region>",
    help="The region of the Nomad servers to forward commands to. "
    + "Overrides the NOMAD_REGION environment variable if set.",
)
@click.option(
    "--namespace",
    metavar="<namespace>",
    help="The target namespace for queries and actions bound to a namespace. "
    + "Overrides the NOMAD_NAMESPACE environment variable if set.",
)
@click.option(
    "--token",
    metavar="<token>",
    help="The SecretID of an ACL token to use to authenticate API requests with. "
    + "Overrides the NOMAD_TOKEN environment variable if set.",
)
@click.option(
    "--meta",
    callback=validate_meta,
    multiple=True,
    metavar="<key>=<value>",
    help="Meta takes a key/value pair separated by \"=\". The metadata key will be "
    + "merged into the job's metadata. The job may define a default value for the "
    + "key which is overridden when dispatching. The flag can be provided more than "
    + "once to inject multiple metadata key/value pairs. Arbitrary keys are not "
    + "allowed. The parameterized job must allow the key to be merged. ",
)
@click.option(
    "--nomad-timeout",
    metavar="<timeout>",
    type=float,
    help="Nomad client API timeout",
)
@click.argument("job", nargs=1)
@click.argument(
    "input",
    nargs=1,
    required=False,
    type=click.File(mode="rb"),
)
def root(**opts: tp.Any) -> None:
    """
    Create an instance of a parameterized nomad <JOB> and wait for its
    completion outputting job's stdout and stderr.
    A data payload to the dispatched instance can be provided via stdin by using
    "-" or by specifying a path to a file. Metadata can be supplied by using
    the --meta flag one or more times.

    An attempt will be made to stop the job if this tool is interrupted with
    a signal.
    """

    payload_b64: tp.Optional[bytes]

    if opts["input"] is not None:
        payload = opts["input"].read()
        # per https://www.nomadproject.io/api/jobs.html#payload
        payload_b64 = base64.b64encode(payload)
        if len(payload_b64) > 15 * 1024:
            raise click.BadParameter("encoded payload size exceeds permitted 15KB.")
    else:
        payload_b64 = None

    nomad_opts: tp.Dict[str, tp.Any] = {}

    for cli_opt_name, nomad_opt_name in [
        ("address", "address"),
        ("region", "region"),
        ("namespace", "namespace"),
        ("token", "token"),
        ("nomad_timeout", "timeout"),
    ]:
        opt_value = opts[cli_opt_name]
        if opt_value is not None:
            nomad_opts[nomad_opt_name] = opt_value

    nomad_api = nomad.Nomad(**nomad_opts)

    try:
        dispatch_job_resp = nomad_api.job.dispatch_job(opts["job"], meta=opts["meta"], payload=payload_b64)
    except nomad.api.exceptions.BaseNomadException as e:
        raise click.ClickException(e.nomad_resp.text)

    logger.debug("dispatch_job response: %s", dispatch_job_resp)

    dispatched_job_id = dispatch_job_resp["DispatchedJobID"]

    try:
        pass
    finally:
        try:
            nomad_api.job.deregister_job(dispatched_job_id)
        except nomad.api.exceptions.BaseNomadException as e:
            logger.error("Failed to deregister dispatched job: %s", e.nomad_resp.text)
