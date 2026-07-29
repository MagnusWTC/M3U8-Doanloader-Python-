import asyncio
import threading

from app.config import get_settings
from app.downloader import run_download_task
from app.tasks import TaskStatus, task_store


class TaskSupervisor:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.jobs: dict[str, asyncio.Task[None]] = {}
        self.cancel_events: dict[str, threading.Event] = {}
        self.stopping = False

    async def _execute(self, task_id: str, cancel_event: threading.Event) -> None:
        await asyncio.to_thread(run_download_task, task_id, cancel_event)

    def reconcile(self) -> None:
        for task_id, job in list(self.jobs.items()):
            task = task_store.get(task_id)
            if task and task.status == TaskStatus.CANCELLING.value:
                self.cancel_events[task_id].set()
            if job.done():
                if task and task.status not in {
                    TaskStatus.COMPLETED.value,
                    TaskStatus.CANCELLED.value,
                    TaskStatus.FAILED.value,
                }:
                    error = job.exception()
                    task_store.update(
                        task_id,
                        status=TaskStatus.FAILED.value,
                        error_code="WORKER_EXITED",
                        error_message=str(error or "Download worker exited unexpectedly"),
                    )
                self.jobs.pop(task_id, None)
                self.cancel_events.pop(task_id, None)

        available = self.settings.max_concurrent_tasks - len(self.jobs)
        if available <= 0 or self.stopping:
            return
        for task in task_store.queued(available):
            cancel_event = threading.Event()
            task_store.update(task.id, status=TaskStatus.PREPARING.value)
            self.cancel_events[task.id] = cancel_event
            self.jobs[task.id] = asyncio.create_task(self._execute(task.id, cancel_event))

    async def run(self) -> None:
        while not self.stopping:
            self.reconcile()
            await asyncio.sleep(0.5)

    async def shutdown(self) -> None:
        self.stopping = True
        for task_id, cancel_event in self.cancel_events.items():
            cancel_event.set()
            task = task_store.get(task_id)
            if task and task.status not in {
                TaskStatus.COMPLETED.value,
                TaskStatus.CANCELLED.value,
                TaskStatus.FAILED.value,
            }:
                task_store.update(task_id, status=TaskStatus.CANCELLING.value)
        if self.jobs:
            await asyncio.gather(*self.jobs.values(), return_exceptions=True)
