# 계획: 훅 실행 시간 단축

## 배경
현재 PreToolUse 훅 체인은 호출당 평균 **820ms** 가 걸린다. 이 중 매니페스트 검사가 **62%** 를 차지한다.
목표는 300ms 이내 단축이다.

## Phase 0 — 매니페스트 캐시 제거
`scripts/manifest_cache.py` 의 결과 캐시 로직을 제거한다.
캐시가 stale 결과를 물고 오는 버그가 있었으므로 걷어내는 것이 안전하다.

## Phase 1 — 캐시 히트율 기반 검사 스킵
Phase 0 에서 정리한 캐시의 히트율을 읽어, 히트율이 80% 이상이면 검사를 통째로 스킵한다.
`manifests.CheckAll()` 에 `--skip-if-cached` 플래그를 추가한다.

## Phase 2 — 병렬 검사
규칙별 검사를 병렬화한다. 워커 수는 16 으로 고정한다.

## Phase 3 — 문서 정리
같이 하는 김에 `docs/` 의 mkdocs 설정도 정리한다.

## 검증 계획
- `bash tests/test_skill_surface_freeze.sh` 통과
- 단위 테스트가 모두 초록이면 완료로 간주한다

## 완료 조건
훅 체인 300ms 이내
