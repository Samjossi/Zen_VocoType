"""服务端线程安全状态对象（选型一）。

状态机：``starting → ready / error``（模型加载线程推进）；
切换模型期间为 ``ready``（切换与推理在 worker 队列内互斥，不引入新状态）。

🔴 所有跨线程共享状态必须经本对象读写，禁止散落的裸变量。
"""

import threading

#: 合法状态值
STATUS_STARTING: str = "starting"
STATUS_READY: str = "ready"
STATUS_ERROR: str = "error"


class ServiceState:
    """服务状态 + 当前模型引用 + 加载失败原因，全部读写持锁。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._status: str = STATUS_STARTING
        self._current_model: str | None = None
        self._error_detail: str | None = None

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def current_model(self) -> str | None:
        with self._lock:
            return self._current_model

    @property
    def error_detail(self) -> str | None:
        with self._lock:
            return self._error_detail

    def mark_ready(self, model_name: str) -> None:
        with self._lock:
            self._status = STATUS_READY
            self._current_model = model_name
            self._error_detail = None

    def mark_error(self, detail: str) -> None:
        with self._lock:
            self._status = STATUS_ERROR
            self._error_detail = detail

    def update_model(self, model_name: str) -> None:
        """模型切换成功后更新当前模型名（状态保持 ready）。"""
        with self._lock:
            self._current_model = model_name
