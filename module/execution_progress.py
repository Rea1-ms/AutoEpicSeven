"""Count normally returned dispatches in the scheduler's current due batch.

The scheduler's success flag only confirms that a task returned without a
framework error. Task entry points may consume business-level results, so this
count is not proof that a game objective or reward was successfully achieved.
"""

from module.runtime_events import runtime_events


class ExecutionProgress:
    def __init__(self, config_name, events=None):
        self.config_name = config_name
        self.events = events if events is not None else runtime_events
        self._tasks = set()
        self._completed = set()
        self._previous_completed_count = 0
        self.task = ''

    @property
    def completed(self):
        return self._previous_completed_count + len(self._completed)

    @property
    def total(self):
        return self._previous_completed_count + len(self._tasks)

    def _publish(self, state):
        self.events.publish_progress(
            self.config_name, state, completed=self.completed,
            total=self.total or None, task=self.task,
        )

    def update_pending(self, commands, skip_restart=False):
        pending = set(commands)
        if skip_restart:
            pending.discard('Restart')
        if not pending:
            # Failed or cancelled dispatches are not normal completions. Clear
            # this batch at a real idle boundary and notify only once.
            finished = bool(self._tasks) and self._tasks <= self._completed
            self.waiting()
            self._tasks.clear()
            self._completed.clear()
            self._previous_completed_count = 0
            return finished
        # Recovery tasks and newly due tasks may join while a batch is running.
        # Keep the successful set across retries; attempts are not work units.
        for command in pending:
            self._queue_task(command)
        self.task = ''
        self._publish('running')
        return False

    def _queue_task(self, command):
        if command in self._completed:
            # The same command can become due again before the scheduler idles.
            # Preserve the prior successful unit while opening a fresh one;
            # otherwise its next failure would inherit the earlier success.
            self._completed.remove(command)
            self._previous_completed_count += 1
        self._tasks.add(command)

    def start_task(self, command):
        self._queue_task(command)
        self.task = command
        self._publish('running')

    def finish_task(self, command, success):
        if success:
            self._completed.add(command)
        self._publish('running')

    def waiting(self):
        self.task = ''
        self._publish('waiting')

    def stop(self, state='stopped'):
        self.task = ''
        self._publish(state)
