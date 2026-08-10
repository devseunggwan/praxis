# 장애 보고: 야간 배치가 간헐적으로 0 row 를 적재

## 증상
`daily_sync` 배치가 하루에 한 번 도는데, 최근 2주 중 4일은 적재 row 가 0 이었다.
실패한 날은 전부 배치 시작 시각이 **00:00~00:10 사이**였고, 성공한 날은 (지연 재기동 탓에) **09:15 이후** 시작이었다.
배치는 실패해도 exit code 0 을 반환해서 알림이 오지 않았다.

## 관련 코드 (발췌)

```python
# sync_job.py
def run_daily_sync():
    target_date = datetime.now().strftime("%Y-%m-%d")
    rows = fetch_rows(target_date)
    if not rows:
        logger.info("no rows for %s", target_date)
        return 0
    write_rows(rows)
    return len(rows)


# fetcher.py
def fetch_rows(date_str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM events WHERE DATE(created_at) = %s",
        (date_str,),
    )
    return cur.fetchall()


# conn.py
_POOL = None

def get_conn():
    global _POOL
    if _POOL is None:
        _POOL = create_pool(size=5, timeout=3)
    return _POOL.acquire()
```

## 팀에서 나온 가설
- A: 커넥션 풀 크기가 5 라서 야간에 고갈된다
- B: `create_pool(timeout=3)` 의 3초 타임아웃이 짧다
- C: 최근 `events` 테이블에 인덱스가 빠져서 쿼리가 느려졌다

## 관측된 추가 사실
- 실패한 4일 모두 `no rows for ...` 로그가 남아 있었다
- 같은 날짜를 인자로 수동 재실행하면 정상적으로 row 가 적재된다
- DB 커넥션 에러 로그는 2주간 0 건
- `events.created_at` 은 UTC 로 저장된다. 배치 서버 타임존은 Asia/Seoul (UTC+9)
