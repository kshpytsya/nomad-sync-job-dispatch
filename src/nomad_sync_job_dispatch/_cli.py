import base64
import json
import logging
import sys
import threading
import time
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
    + "allowed. The parameterized job must allow the key to be merged.",
)
@click.option(
    "--nomad-timeout",
    metavar="<sec>",
    type=float,
    help="Nomad client API timeout.",
)
@click.option(
    "--alloc-timeout",
    metavar="<sec>",
    type=float,
    show_default=True,
    default=15.0,
    help="Time to wait for job allocation to be created.",
)
@click.option(
    "--alloc-timeout-step",
    metavar="<sec>",
    type=float,
    show_default=True,
    default=2.0,
    help="Job allocation polling interval.",
)
@click.option(
    "--task",
    metavar="<task>",
    multiple=True,
    help="Task to monitor. May be specified multiple times.",
)
@click.option(
    "--prefix-task/--no-prefix-task",
    default=False,
    help="Prepend task name before every output line",
)
@click.option(
    "--log-poll-interval",
    metavar="<sec>",
    type=float,
    show_default=True,
    default=2.0,
    help="Log polling interval.",
)
@click.option(
    "--alloc-poll-interval",
    metavar="<sec>",
    type=float,
    show_default=True,
    default=2.0,
    help="Allocation status polling interval.",
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
        raise click.ClickException(f"failed to dispatch job: {e.nomad_resp.text}")

    logger.debug("dispatch_job response: %s", dispatch_job_resp)

    dispatched_job_id = dispatch_job_resp["DispatchedJobID"]
    dispatched_job_eval_id = dispatch_job_resp["EvalID"]

    try:
        def wait_for_alloc() -> tp.List[tp.Any]:
            deadline = time.time() + opts["alloc_timeout"]
            while True:
                remaining = deadline - time.time()
                if remaining < 0:
                    raise click.ClickException("timed out waiting for allocation to be created")

                try:
                    allocations = nomad_api.evaluation.get_allocations(dispatched_job_eval_id)
                except nomad.api.exceptions.BaseNomadException as e:
                    raise click.ClickException(f"failed getting evaluation allocations: {e.nomad_resp.text}")

                if allocations:
                    assert isinstance(allocations, list)
                    if all(i.get("TaskStates") for i in allocations):
                        return allocations

                logger.debug(f"waiting for allocation to appear, {remaining:1.0f}s remaining till deadline")
                time.sleep(min(remaining, opts["alloc_timeout_step"]))

        allocations = wait_for_alloc()
        if len(allocations) != 1:
            raise click.ClickException(f"expected a single allocation to appear, but got {len(allocations)}")

        allocation = allocations[0]
        allocation_id = allocation["ID"]

        logger.debug(f"got allocation {allocation_id}")

        if opts["task"]:
            tasks_to_monitor = []
            for i in opts["task"]:
                if i not in allocation["TaskStates"]:
                    raise click.ClickException(f"task \"{i}\" is not found")
                tasks_to_monitor.append(i)
        else:
            tasks_to_monitor = sorted(allocation["TaskStates"])

        if opts["prefix_task"]:
            max_task_name_len = max(len(i) for i in tasks_to_monitor)
        else:
            max_task_name_len = 0

        line_buffering = len(tasks_to_monitor) > 1
        threads = []
        stop_streaming = threading.Event()

        def streaming_func(task: str, log_type: int) -> None:
            offset = 0
            log_poll_interval = opts["log_poll_interval"]
            type_str = ["stdout", "stderr"][log_type]
            dest_fd = [sys.stdout, sys.stderr][log_type]
            tail = b""

            if max_task_name_len:
                line_prefix = f"{task:{max_task_name_len}}:".encode()
            else:
                line_prefix = b""

            stop_on_empty_response = False

            while True:
                try:
                    response = nomad_api.client.stream_logs.stream(
                        id=allocation_id,
                        task=task,
                        offset=offset,
                        type=type_str,
                    )
                except nomad.api.exceptions.BaseNomadException as e:
                    logger.error(
                        f"log streaming failed (alloc={allocation_id}, task={task}, type={type}): {e.nomad_resp.text}",
                    )
                    break

                if response:
                    parsed_response = json.loads(response)
                    data = base64.b64decode(parsed_response["Data"])

                    if line_prefix or line_buffering:
                        for line in (tail + data).splitlines(keepends=True):
                            if line.endswith(b"\n"):
                                dest_fd.buffer.write(line_prefix)
                                dest_fd.buffer.write(line)
                            else:
                                tail = line
                    else:
                        dest_fd.buffer.write(data)

                    dest_fd.flush()
                    offset = parsed_response["Offset"]
                else:
                    if stop_on_empty_response:
                        break

                if not stop_on_empty_response:
                    if stop_streaming.wait(log_poll_interval):
                        stop_on_empty_response = True

            if tail:
                # Note: it appears that Nomad always adds a trailing "\n" at the end of log
                # be let us not rely on that
                dest_fd.buffer.write(line_prefix)
                dest_fd.buffer.write(tail)
                dest_fd.buffer.write(b"\n")

        for task_to_monitor in tasks_to_monitor:
            for log_type in [0, 1]:
                threads.append(threading.Thread(target=streaming_func, args=(task_to_monitor, log_type)))

        for thread in threads:
            thread.start()

        alloc_poll_interval = opts["alloc_poll_interval"]

        while True:
            try:
                # TODO find a way to use "blocking query" mechaninsm which
                # doesn't seem to be directly supported by python-nomad
                allocation_status = nomad_api.allocation.get_allocation(allocation_id)
            except nomad.api.exceptions.BaseNomadException as e:
                raise click.ClickException(f"failed getting allocation status: {e.nomad_resp.text}")

            allocation_client_status = allocation_status["ClientStatus"]
            if allocation_client_status in ["complete", "failed", "lost"]:
                break

            time.sleep(alloc_poll_interval)

        logger.debug("allocation complete with status \"%s\", stopping streaming threads", allocation_client_status)
        stop_streaming.set()

        for thread in threads:
            thread.join()

        if allocation_client_status != "complete":
            sys.exit(1)
    finally:
        try:
            nomad_api.job.deregister_job(dispatched_job_id)
        except nomad.api.exceptions.BaseNomadException as e:
            logger.error("failed to deregister dispatched job: %s", e.nomad_resp.text)
