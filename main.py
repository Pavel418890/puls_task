import json
import threading
from datetime import timedelta
from itertools import cycle
from typing import Optional, Union
from urllib.request import urlopen

from redis.client import PubSubWorkerThread
from redis import StrictRedis, WatchError


class AtomicBool:
    def __init__(self, value: bool):
        self.lock = threading.Lock()
        self.value = value

    def get(self) -> bool:
        with self.lock:
            return self.value

    def set(self, val: bool):
        with self.lock:
            self.value = val


class PulsTask:
    WAIT_TIME = timedelta(seconds=60)
    REQUESTS_LIMIT = 30

    def __init__(self, conn: StrictRedis, api_hosts: Union[set, list, tuple]):
        self.conn = conn
        self.pubsub: Optional[PubSubWorkerThread] = None
        self.urls = {}

        for url in api_hosts:
            self.urls[url] = AtomicBool(True)
        self.round_robin = cycle(self.urls)
        self.round_robin_lock = threading.Lock()

        self.last_expired_key = None
        self.last_expired_key_lock = threading.Lock()

        # Блокирует поток/процесс в случае превышения лимита запросов
        self.wait_event = threading.Event()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self):
        """
        Подписка в отдельном потоке
        на события истечения срока ключей(ключ - API host).
        При возникновении события вызывает коллбэк.
        """
        pubsub = self.conn.pubsub()
        pubsub.psubscribe(**{
            "__keyevent@*__:expired": self._handle_expiration
        })
        self.pubsub = pubsub.run_in_thread(sleep_time=0.03)

    def stop(self):
        self.pubsub.stop()
        self.wait_event.clear()
        self.conn.close()

    def get_post(self, post_id: int) -> dict:
        # По очереди берем ключи из безконечного
        # генератора ключей
        with self.round_robin_lock:
            url = next(self.round_robin)

        while True:
            # Запрос на счетчик в редис, если счетчик
            # не превысил лимит, то выполняем запрос
            if self._get_current(url) >= self.REQUESTS_LIMIT:
                # Если счетчик больше лимита в цикле
                # проходимся по локальному хранилищу свободных
                # для запросов хостов и берем первый, и пробуем
                # выполнить запрос еще раз
                found = False
                for u in self.urls:
                    if self.urls[u].get():
                        url = u
                        found = True
                        break
                if found:
                    continue
                # На всех хостах превышен лимит запросов.
                # Блокируемся и ждем освобождения блокировки.
                self.wait_event.wait()
                with self.last_expired_key_lock:
                    # Данный ключ(хост) искет по ttl -
                    # пытаемся сделать запрос.
                    url = self.last_expired_key
                    continue

            resp = urlopen(f"{url}/posts/{post_id}")
            return json.loads(resp.read())

    def _handle_expiration(self, msg: dict) -> None:
        """
        Коллбэк для ключей удаленных по ttl.
        Атомарно помечает ключ(API хост) как свободный
        для запросов, если ключ находится в локальном хранилище API хостов.
        Отпускает блокировку запросов
        """
        expired_key = msg["data"].decode("UTF-8")
        if expired_key in self.urls:
            with self.last_expired_key_lock:
                self.last_expired_key = expired_key

            self.urls[expired_key].set(True)
            self.wait_event.set()

    def _get_current(self, url: str) -> int:
        """
        По умолчанию, в рамках редис транзакции
        пытается увеличить счетчик запросов для API хоста
        и вернуть его значение.

        Если данная операция вызывается первый раз, то
        выставляет ttl(time-to-live) для данного хоста.

        В случае, если другой поток/процесс изменил в ходе
        выполнения транзации значение счетчика, то раизится
        WatchError, операция начинается с начала, пока не будет
        выполнена.

        Для ключей с счетчиком превысившим значение атомарно
        изменяется состояние доступности хоста в локальном хранилище
        ключей (API хостов)
        """
        while True:
            with self.conn.pipeline() as pipe:
                try:
                    pipe.watch(url)
                    count = pipe.get(url)

                    pipe.multi()
                    pipe.incr(url)

                    if count is None:
                        pipe.expire(url, self.WAIT_TIME)

                    result = pipe.execute()[0]
                    if result >= self.REQUESTS_LIMIT:
                        self.urls[url].set(False)

                    return result
                except WatchError:
                    continue
