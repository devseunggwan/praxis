# PostToolUse 동일 실패 패턴 Advisory

Supported hosts: all

`hooks/second-failure-advisory` is a PostToolUse 보조 훅입니다. 동일
`tool_name + error_signature` 조합이 같은 세션에서 반복 실패했을 때
2회째 실패에 한해 stdout의 `hookSpecificOutput.additionalContext`로 advisory를
출력해, 원인 분석 없이 동일 실패를 무한 재시도하는 패턴을 줄입니다.

## Why this exists

최근 일부 도구 호출에서 동일한 원인으로 동일 오류가 반복되는데도 매번 새
실패로 처리되어, 사용자가 어떤 조치가 필요한지 놓치고 즉시 재시도 루프를
유도하는 패턴이 발견되어 이슈로 분리되었습니다.

`second-failure-advisory`는 경량 추적 기반으로만 동작하므로 툴 실행을
차단하지 않고(항상 exit 0), 2회째 반복만 알리는 advisory로 동작합니다.

## Covered surface

- Event: `PostToolUse`
- Matcher: `all tools` — `hooks/manifest.json` 항목에 `matcher` 키를 두지
  않습니다. 명시 목록을 쓰면 MCP 도구·`WebFetch`·`NotebookEdit`처럼 열거되지
  않은 이름의 반복 실패가 matcher 단계에서 통째로 누락됩니다.

## Failure 판단

`tool_response` 기준:

- `isError is True`이면 실패
- `interrupted is True`이면 실패
- `exit`가 정수 0이 아니면 실패
- 위 항목이 없고 `error`/`stderr`에 비어있지 않은 텍스트가 있으면 실패
- `output`/`stdout`만 있는 응답은 성공으로 처리

## Signature 산정

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

## Counting semantics — 세션 누적, 연속 아님

카운터는 `(tool_name, signature)` 쌍별 **세션 누적** 값입니다. 중간에 성공이나
다른 실패가 끼어들어도 카운터는 리셋되지 않으며, 같은 쌍의 2회째 실패에서
advisory가 발화합니다. 이는 issue #944가 지정한 동작("`(tool_name,
error_signature)`를 세션 스코프로 카운트하고 2회차에 advisory")입니다.

"연속 실패"가 아니라 "세션 내 2회째 동일 실패"가 발화 조건이므로, 메시지와
문서 모두 연속(consecutive)이라는 표현을 쓰지 않습니다.

## Output behavior

다음 경우에만 advisory를 출력합니다.

- 동일 `session_id` 기준 2번째 실패 (`(tool_name, signature)` 조합)
- 이전 카운트가 1일 때

상태 저장(`os.replace` 기반 원자적 교체)이 성공한 뒤에만 advisory를
출력합니다. 저장에 실패하면 카운터가 남지 않아 같은 advisory가 다음 실패에서
중복 발화할 수 있으므로, 저장 실패 시에는 무음 처리합니다.

원자적인 것은 교체(rename)뿐이며, 읽기-증가-쓰기-발화 전체는 프로세스 간에
직렬화되지 않습니다. 한 세션에서 도구 호출이 병렬로 끝나면 PostToolUse는
호출마다 별도 프로세스로 돌므로, 저장된 count가 1일 때 두 프로세스가 함께
1을 읽어 각자 2를 쓰고 **둘 다 advisory를 낼 수 있습니다**. 반대로 서로 다른
쌍의 동시 실패는 한쪽 증가분을 덮어써 advisory가 한 번 늦어질 수 있습니다.
"3회째 이상 무음"은 순차 실행 기준의 계약이며, 이 창에서는 지켜지지 않습니다.

lock은 두지 않습니다. 피해가 advisory 한 줄 중복 또는 한 박자 지연에 그치고
이 훅은 차단하지 않기 때문이며, 무엇보다 동일한 읽기-수정-쓰기 + `os.replace`
패턴을 쓰는 훅이 이 레포에 7개 있고 파일 락 사용은 0건이라, 이 훅 하나만
직렬화하면 문제는 남긴 채 패턴만 갈라집니다. 직렬화가 필요하다는 판단이
서면 7개 공통 정책으로 다루는 것이 맞습니다.

출력은 `stdout`에 `hookSpecificOutput.additionalContext`로 1줄 기록합니다
(DESIGN.md의 PostToolUse 교정 방출 규약, `builtin-task-postuse`와 동일 형태).
exit 0인 PostToolUse 훅의 `stderr`는 디버그 로그로만 가고 모델에 도달하지
않으므로, stderr로 내보내면 이 훅이 존재하는 이유인 재시도 루프 교정이
일어나지 않습니다.

```json
{"continue": true, "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "[second-failure-advisory] 동일한 오류 패턴으로 세션 내 2회째 실패가 감지되었습니다. … signature=<sig_prefix> Reference: <path?> — …"}}
```

`reference`는 실패 텍스트의 `Reference:` label, `hooks/...` 또는 `*spec.md`
경로에서 먼저 추출하고, 없으면 `tool_input.file_path/path/target`을 사용합니다.
경로가 추출되면 해당 파일을 read하고 차단 판정 술어를 한 줄로 재진술한 뒤
재시도하라는 지시를 같이 출력합니다. 경로가 없으면 판정 술어 재진술만
요구합니다.

다음 경우는 무음(fail-open) 처리이며 exit 0으로 종료됩니다.

- malformed stdin / 비-JSON 입력
- `session_id` 없음
- `tool_name` 없음
- 성공 응답
- 첫 번째 실패
- 저장 실패(상태 파일 I/O 실패)

## State

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

## Privacy

- 원문 오류 텍스트 자체를 기록하지 않고, 정규화된 signature의 hash를 저장해
  민감 로그 누출을 줄입니다.

## Tests

Run:

```bash
bash tests/hooks/postuse-correction/test_second_failure_advisory.sh
```

필수 커버:

- 1회 실패: advisory 없음
- 2회 실패(동일 signature): advisory 출력
- 3회째 이상(동일 쌍): 추가 advisory 없음
- 동일 시그니처에서 tool_name이 다르면 advisory 없음
- 경로/해시/타임스탬프만 바뀐 2회 실패도 advisory 출력
- 사이에 성공/다른 실패가 끼어도 같은 쌍의 2회째에 advisory 출력
- 상태 저장 실패 시 advisory 무음
- `stdout`/`output`만 있는 성공 응답은 반복돼도 무음
- 실패 텍스트의 `Reference:` 경로가 advisory와 재진술 지시에 포함됨
- 비실패/비정상 입력은 fail-open
