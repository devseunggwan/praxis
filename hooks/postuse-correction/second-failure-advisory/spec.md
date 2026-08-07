# PostToolUse 동일 실패 패턴 Advisory

Supported hosts: all

`hooks/second-failure-advisory` is a PostToolUse 보조 훅입니다. 동일
`tool_name + error_signature` 조합이 같은 세션에서 반복 실패했을 때
2회째 실패에 한해 stderr에 advisory를 출력해, 원인 분석 없이 동일 실패를
무한 재시도하는 패턴을 줄입니다.

### Why this exists

최근 일부 도구 호출에서 동일한 원인으로 동일 오류가 반복되는데도 매번 새
실패로 처리되어, 사용자가 어떤 조치가 필요한지 놓치고 즉시 재시도 루프를
유도하는 패턴이 발견되어 이슈로 분리되었습니다.

`second-failure-advisory`는 경량 추적 기반으로만 동작하므로 툴 실행을
차단하지 않고(항상 exit 0), 2회째 반복만 알리는 advisory로 동작합니다.

### Covered surface

- Event: `PostToolUse`
- Matcher: `all tools` (hook가 등록된 이벤트의 tool_name 제약은 별도 없음)

### Failure 판단

`tool_response` 기준:

- `isError is True`이면 실패
- `interrupted is True`이면 실패
- `exit`가 정수 0이 아니면 실패
- 위 항목이 없고 `error`/`stderr`/`output`/`stdout`에 비어있지 않은 텍스트가
  있으면 실패

### Signature 산정

`tool_response`에서 실패 텍스트 후보를 다음 순서로 추출합니다.

1. `error`
2. `stderr`
3. `output`
4. `stdout`

추출 실패 시 빈 문자열을 쓰고, 실패 키를 보정합니다.

동일 실패를 추정하기 위해 아래 토큰은 정규화합니다.

- 경로 토큰: `<path>`
  - Unix-like `/...`
  - Windows `C:\...`
- UUID: `<uuid>`
- 16자리 이상 16진수 문자열: `<hash>`
- 타임스탬프: `<ts>`
- `*id*` 패턴: `<id>` 토큰으로 정규화

정규화 후 텍스트를 소문자화하고 최대 길이를 제한합니다.
최종 signature은 `sha1(f"{tool_name}\\0{normalized_signature}")`입니다.

### Output behavior

다음 경우에만 advisory를 출력합니다.

- 동일 `session_id` 기준 2번째 실패 (`(tool_name, signature)` 조합)
- 이전 카운트가 1일 때

출력은 `stderr`에 아래 형태로 1줄 기록합니다.

```
[second-failure-advisory] 동일한 오류 패턴으로 연속 실패가 감지되었습니다. ... signature=<sig_prefix> | reference=<path?>
```

`reference`는 `tool_input.file_path`가 있으면 같이 출력합니다.

다음 경우는 무음(fail-open) 처리이며 exit 0으로 종료됩니다.

- malformed stdin / 비-JSON 입력
- `session_id` 없음
- `tool_name` 없음
- 성공 응답
- 첫 번째 실패
- 저장 실패(상태 파일 I/O 실패)

### State

기준 파일:

`<cache>/second-failure-advisory-<session_id>.json`

`PRAXIS_SECOND_FAILURE_ADVISORY_FILE`이 있으면 해당 경로를 우선 사용합니다
(테스트/격리 목적).

포맷 예시:

```json
{
  "schema_version": 1,
  "failures": {
    "Bash|deadbeef": 2
  }
}
```

### Privacy

- 원문 오류 텍스트 자체를 기록하지 않고, 정규화된 signature의 hash를 저장해
  민감 로그 누출을 줄입니다.

### Tests

Run:

```bash
bash tests/hooks/postuse-correction/test_second_failure_advisory.sh
```

필수 커버:

- 1회 실패: advisory 없음
- 2회 실패(동일 signature): advisory 출력
- 동일 시그니처에서 tool_name이 다르면 advisory 없음
- 경로/해시/타임스탬프만 바뀐 2회 실패도 advisory 출력
- 비실패/비정상 입력은 fail-open
