"""
Alasio worker entry for the AES mod.

Alasio spawns a worker process and runs:
    mod_entry() -> chdir(mod_root) -> import module.main -> Scheduler(config_name).run()

By the time this file is imported, cwd and sys.path[0] are the AES repo root and
env.PROJECT_ROOT points at the Alasio project root (where config DBs live).

This file bridges Alasio's worker protocol to the legacy AES scheduler:
- BackendBridge.scheduler_stopping -> AzurLaneAutoScript.stop_event / AzurLaneConfig.stop_event
- legacy 'alas' logger records      -> BackendBridge.send_log() GUI stream
- wait_until()                      -> send_worker_state('scheduler-waiting' / 'running')
"""
import logging
import re
import time

# Strip rich markup like [bold]...[/bold] when a record was logged with extra={'markup': True}
_RICH_MARKUP = re.compile(r'\[/?[a-z][^\]]*\]')


def _format_exception(exc_info):
    try:
        from alasio.logger.logger import rich_formatter
        result = rich_formatter(exc_info)
        if isinstance(result, tuple):
            # returns (exception_rich, exception_plain), GUI expects the plain one
            return result[1]
        return result
    except Exception:
        import traceback
        return ''.join(traceback.format_exception(*exc_info))


class BackendLogHandler(logging.Handler):
    """Forward legacy 'alas' logger records to the Alasio GUI log stream."""

    def __init__(self, backend):
        super().__init__()
        self.backend = backend

    def emit(self, record):
        # Never let GUI log streaming break the scheduler
        try:
            msg = record.getMessage()
            if getattr(record, 'markup', False):
                msg = _RICH_MARKUP.sub('', msg)
            event = {'t': record.created, 'l': record.levelname, 'm': msg}
            if record.exc_info and record.exc_info != (None, None, None):
                event['e'] = _format_exception(record.exc_info)
            self.backend.send_log(event)
        except Exception:
            pass


def install_log_bridge(backend):
    """
    Attach GUI forwarding to the legacy logger.

    - Normal records go through BackendLogHandler.
    - logger.rule() (used by logger.hr level 0/1/2) renders via rich console only and
      bypasses logging records, so wrap it to mirror rules as raw lines into the GUI.
    """
    from module.logger import logger

    logger.addHandler(BackendLogHandler(backend))

    original_rule = logger.rule

    def bridge_rule(title="", *, characters="─", style="rule.line", end="\n", align="center"):
        original_rule(title=title, characters=characters, style=style, end=end, align=align)
        char = {'═': '=', '─': '-'}.get(characters, characters[:1] or '-')
        title_ = str(title)
        line = f' {title_} '.center(100, char) if title_ else char * 100
        backend.send_log({'t': time.time(), 'l': 'INFO', 'm': line, 'r': 1})

    logger.rule = bridge_rule


class Scheduler:
    """Entry class required by alasio.backend.worker.bridge.mod_entry()."""

    def __init__(self, config_name):
        self.config_name = config_name

    def run(self):
        from alasio.backend.worker.bridge import BackendBridge
        backend = BackendBridge()

        install_log_bridge(backend)

        from aes import AutoEpicSeven
        from module.config.config import AzurLaneConfig

        class BridgeAES(AutoEpicSeven):
            def wait_until(self, future):
                backend.send_worker_state('scheduler-waiting')
                try:
                    return super().wait_until(future)
                finally:
                    backend.send_worker_state('running')

        # GUI stop button sets scheduler_stopping, legacy code polls stop_event.
        # Old webui wired both classes, keep the same coverage.
        BridgeAES.stop_event = backend.scheduler_stopping
        AzurLaneConfig.stop_event = backend.scheduler_stopping

        app = BridgeAES(config_name=self.config_name)
        app.loop()
