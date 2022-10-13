### Необходимые зависимости:
[Redis](https://redis.io/download/) > v.6.0.9 или докер контейнер
```shell
docker pull redis
docker run -d --name <container_name> -p <port>:6379 -v <path_to_project>/redis:/usr/local/etc/redis redis redis-server /usr/local/etc/redis/redis.conf
```

*<small>In Redis versions before 6.0.9, an expired key would not cause a transaction to be aborted.</small>

[redis-py](https://pypi.org/project/redis/)
```shell
pip install redis
```
### Примеры:
Однопоточный запуск
```python
hosts = (
    "https://jsonplaceholder.typicode.com",
    "http://188.127.251.4:8240",
)

with PulsTask(StrictRedis(host="localhost", port=6379, db=0), hosts) as pt:
    for i in range(1, 32):
        return ("RESULT %s" % (pt.get_post(i)))
```
Многопоточный запуск

```python
import os

hosts = (
    "https://jsonplaceholder.typicode.com",
    "http://188.127.251.4:8240",
)

with PulsTask(conn, hosts) as pt:
    def run(t: PulsTask, t_num):
        for i in range(1, 63):
            return ("PROCESS %s RESULT %s" % (t_num, t.get_post(i)))


    tasks = []

    for i in range(os.cpu_count()):
        task = threading.Thread(target=run, args=(pt, "Thread %d" % i))
        tasks.append(task)
        task.start()

    for task in tasks:
        task.join()

```
Запуск в процессах
```python
hosts = (
    "https://jsonplaceholder.typicode.com",
    "http://188.127.251.4:8240",
)

redis_pool = ConnectionPool(
    host=os.getenv("REDIS_HOST") or "localhost",
    port=os.getenv("REDIS_PORT") or 6379,
    db=os.getenv("REDIS_DB") or 0
)

def run(id_: int):
        with PulsTask(StrictRedis(connection_pool=redis_pool)) as pt:
            for i in range(1, 63):
                (f"TASK {id_} RESULT{pt.get_post(i)}")

    tasks = []
    for i in range(os.cpu_count()):
        p = multiprocessing.Process(target=run, args=(i, ))
        tasks.append(p)

    for t in tasks:
        t.start()

    for t in tasks:
        t.join()
```