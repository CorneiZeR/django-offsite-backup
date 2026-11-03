"""Subprocess pipelines with ``pipefail`` semantics, and no shell.

A stage that dies mid-stream must fail the whole pipeline: otherwise a ``pg_dump`` that
broke halfway leaves a small, valid, truncated ``.age`` and an exit status of zero.
Every stage's status is read, and its stderr kept (the tail) for the error message.
Secrets travel in ``env``, never in argv: argv is visible in ``ps`` and error text is
posted to a chat.
"""

import contextlib
import os
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import IO

from offsite_backup.exceptions import PipelineError

__all__ = ('Pipeline', 'feed', 'run_into')

#: how much of a failed stage's stderr an error carries
_STDERR_TAIL = 2000


class Pipeline:
    """``stage1 | stage2 | ...`` started with a given stdin and stdout."""

    def __init__(
        self,
        stages: Sequence[Sequence[str]],
        *,
        stdin: IO[bytes] | int | None,
        stdout: IO[bytes] | int | None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        """Start every stage, wiring each one's stdout into the next one's stdin."""
        if not stages:
            msg = 'a pipeline needs at least one stage'
            raise ValueError(msg)
        self.stages = [list(stage) for stage in stages]
        self._errors: list[IO[bytes]] = []
        self.processes: list[subprocess.Popen[bytes]] = []
        environment = {**os.environ, **env} if env else None
        upstream: IO[bytes] | int | None = stdin
        try:
            for index, stage in enumerate(self.stages):
                last = index == len(self.stages) - 1
                error = tempfile.TemporaryFile()  # noqa: SIM115 - closed in finish()
                self._errors.append(error)
                process = subprocess.Popen(
                    stage,
                    stdin=upstream,
                    stdout=stdout if last else subprocess.PIPE,
                    stderr=error,
                    env=environment,
                )
                if index > 0 and self.processes[-1].stdout is not None:
                    # the parent's copy closed, so the upstream sees SIGPIPE if this stage dies
                    self.processes[-1].stdout.close()
                self.processes.append(process)
                upstream = process.stdout
        except OSError as error:
            self.kill()
            msg = f'`{self.stages[len(self.processes)][0]}` could not be started: {error.strerror or error}'
            raise PipelineError(msg) from error

    @property
    def stdin(self) -> IO[bytes]:
        """The first stage's stdin, when the pipeline was started with ``stdin=PIPE``."""
        handle = self.processes[0].stdin
        if handle is None:
            msg = 'this pipeline was not started with stdin=PIPE'
            raise RuntimeError(msg)
        return handle

    def kill(self) -> None:
        """Stop every stage that is still running."""
        for process in self.processes:
            with contextlib.suppress(OSError):
                process.kill()
        for process in self.processes:
            with contextlib.suppress(OSError):
                process.wait()
        for error in self._errors:
            error.close()

    def finish(self) -> None:
        """Wait for every stage; raise naming the first one that failed."""
        if self.processes[0].stdin is not None and not self.processes[0].stdin.closed:
            self.processes[0].stdin.close()
        statuses = [process.wait() for process in self.processes]
        try:
            for stage, status, error in zip(self.stages, statuses, self._errors, strict=True):
                if status != 0:
                    error.seek(0)
                    tail = error.read().decode(errors='replace').strip()[-_STDERR_TAIL:]
                    msg = f'`{stage[0]}` exited with status {status}' + (f': {tail}' if tail else '')
                    raise PipelineError(msg)
        finally:
            for error in self._errors:
                error.close()


def run_into(
    stages: Sequence[Sequence[str]],
    *,
    into: Path,
    source: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Run a pipeline from ``source`` (or nothing) into the file ``into``; return ``into``."""
    with contextlib.ExitStack() as stack:
        stdin = stack.enter_context(source.open('rb')) if source else subprocess.DEVNULL
        stdout = stack.enter_context(into.open('wb'))
        Pipeline(stages, stdin=stdin, stdout=stdout, env=env).finish()
    return into


@contextlib.contextmanager
def feed(
    stages: Sequence[Sequence[str]],
    *,
    into: Path,
    env: Mapping[str, str] | None = None,
) -> Iterator[IO[bytes]]:
    """Yield the pipeline's stdin; on exit wait for it and raise if any stage failed."""
    with into.open('wb') as stdout:
        pipeline = Pipeline(stages, stdin=subprocess.PIPE, stdout=stdout, env=env)
        try:
            yield pipeline.stdin
        except BrokenPipeError:
            # a stage died while we wrote to it; its own status says why
            pipeline.finish()
            raise
        except BaseException:
            pipeline.kill()
            raise
        pipeline.finish()
